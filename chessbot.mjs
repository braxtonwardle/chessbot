import makeWASocket, {
    useMultiFileAuthState,
    downloadMediaMessage,
    fetchLatestBaileysVersion,
    DisconnectReason
} from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import qrcode from 'qrcode-terminal';
import P from 'pino';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { loadPuzzleState, savePuzzleState } from './puzzleState.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));


// ============================================================
// PYTHON CHESS ENGINE
// ============================================================

console.log('Starting chess engine...');

const PYTHON_BIN = process.env.PYTHON || 'python';

const python = spawn(PYTHON_BIN, [path.join(__dirname, 'chessbot.py')]);

let pythonReady = false;
let stdoutBuffer = '';
let stderrBuffer = '';

python.stderr.on('data', (data) => {
    stderrBuffer += data.toString();

    const lines = stderrBuffer.split('\n');
    stderrBuffer = lines.pop();

    for (const line of lines) {
        const message = line.trim();

        if (!message) {
            continue;
        }

        if (message === 'READY') {
            pythonReady = true;
            console.log('Chess model is ready.');
            continue;
        }

        console.log(`Python: ${message}`);
    }
});

// Membership means "still waiting": a reply whose id is absent gets dropped
// rather than handed to the wrong request.
const pendingAnalyses = new Map();
let nextRequestId = 1;

python.stdout.on('data', (data) => {
    stdoutBuffer += data.toString();

    const lines = stdoutBuffer.split('\n');
    stdoutBuffer = lines.pop();

    for (const line of lines) {
        const reply = line.trim();

        if (reply) {
            routeAnalysisReply(reply);
        }
    }
});

function routeAnalysisReply(reply) {

    const separator = reply.indexOf('|');
    const requestId = separator === -1 ? NaN : Number(reply.slice(0, separator));

    if (!Number.isInteger(requestId)) {
        console.log(`Python result (untagged): ${reply}`);
        return;
    }

    const body = reply.slice(separator + 1);
    const pending = pendingAnalyses.get(requestId);

    if (!pending) {
        console.log(`Discarded reply for request ${requestId} (already timed out): ${body}`);
        return;
    }

    pendingAnalyses.delete(requestId);
    pending.settle(body);
}

python.on('error', (error) => {
    console.error(`Could not start ${PYTHON_BIN} (${error.message}). Set PYTHON to override.`);
    process.exit(1);
});

// Exit rather than run on: nothing can be answered without the helper, and
// start-chessbot.bat restarts node.
python.on('close', (code) => {
    console.error(`Python exited with code ${code}. Restarting.`);
    process.exit(1);
});

const ANALYSIS_TIMEOUT_MS = 20000;

// Still needed after request ids: chessbot.py handles one line at a time, so
// queueing keeps ANALYSIS_TIMEOUT_MS timing the analysis rather than the wait.
let analysisQueue = Promise.resolve();

function analyzeImage(imagePath, sideToMove) {

    const result = analysisQueue.then(() => runAnalysis(imagePath, sideToMove));

    // Keep the queue moving even if this request fails -- one rejected
    // analysis shouldn't jam up everything queued behind it.
    analysisQueue = result.catch(() => {});

    return result;
}

function runAnalysis(imagePath, sideToMove) {

    return new Promise((resolve, reject) => {

        if (!pythonReady) {
            reject(new Error('Chess model is not ready yet.'));
            return;
        }

        const requestId = nextRequestId++;

        const timeoutHandle = setTimeout(() => {
            // Makes the late reply harmless -- no waiter left to give it to.
            pendingAnalyses.delete(requestId);
            reject(new Error('Analysis timed out.'));
        }, ANALYSIS_TIMEOUT_MS);

        pendingAnalyses.set(requestId, {
            settle(body) {
                clearTimeout(timeoutHandle);

                if (body.startsWith('ERROR:')) {
                    reject(new Error(body));
                } else {
                    resolve(body);
                }
            }
        });

        python.stdin.write(`${requestId}|${imagePath}|${sideToMove}\n`);
    });
}


// ============================================================
// DOWNLOAD AN IMAGE (ATTACHED, OR QUOTED/REPLIED-TO)
// ============================================================

function saveImageBuffer(buffer, mimetype, groupId) {

    const extension = (mimetype || 'image/jpeg').split('/')[1] || 'jpg';
    const safeGroupId = groupId.replace(/[^a-zA-Z0-9]/g, '');

    // Timestamped filename -- each request downloads its own image fresh,
    // so nothing can be silently overwritten by unrelated messages the
    // way a single per-group "latest image" slot could be.
    const imagePath = path.join(
        __dirname,
        `received_${safeGroupId}_${Date.now()}.${extension}`
    );

    fs.writeFileSync(imagePath, buffer);

    return imagePath;
}

// Downloads the image a message is replying to, using the mediaKey/url
// embedded in the quoted content itself (Baileys can download from a
// reconstructed message object without needing the original live message).
async function downloadQuotedImage(contextInfo, groupId) {

    const quotedImageMessage = contextInfo?.quotedMessage?.imageMessage;

    if (!quotedImageMessage) {
        return null;
    }

    const syntheticMessage = {
        key: {
            remoteJid: groupId,
            id: contextInfo.stanzaId,
            fromMe: false,
            participant: contextInfo.participant
        },
        message: contextInfo.quotedMessage
    };

    let buffer;

    try {
        buffer = await downloadMediaMessage(
            syntheticMessage,
            'buffer',
            {},
            { logger: P({ level: 'silent' }) }
        );
    } catch (downloadError) {
        console.error(
            'Quoted image download failed:',
            downloadError.message || downloadError
        );
        return null;
    }

    if (!buffer || buffer.length === 0) {
        console.error('Quoted image download returned no data.');
        return null;
    }

    const imagePath = saveImageBuffer(buffer, quotedImageMessage.mimetype, groupId);

    console.log(`Saved replied-to image: ${imagePath}`);

    return imagePath;
}

// Downloads an image attached directly to a message (as opposed to a
// quoted/replied-to one) -- lets a puzzle image and its "chessbot puzzle"
// trigger arrive together as that image's own caption, instead of
// requiring a separate reply.
async function downloadDirectImage(message, groupId) {

    const imageMessage = message.message?.imageMessage;

    if (!imageMessage) {
        return null;
    }

    let buffer;

    try {
        buffer = await downloadMediaMessage(
            message,
            'buffer',
            {},
            { logger: P({ level: 'silent' }) }
        );
    } catch (downloadError) {
        console.error(
            'Attached image download failed:',
            downloadError.message || downloadError
        );
        return null;
    }

    if (!buffer || buffer.length === 0) {
        console.error('Attached image download returned no data.');
        return null;
    }

    const imagePath = saveImageBuffer(buffer, imageMessage.mimetype, groupId);

    console.log(`Saved directly-attached image: ${imagePath}`);

    return imagePath;
}


// ============================================================
// PUZZLE ROUNDS
// ============================================================
//
// "chessbot puzzle" or "chessbot poll" (attached to or replying to an
// image) starts a round: the bot re-shares the image with instructions,
// then collects answers people
// DM it privately -- so nobody spoils the puzzle by answering in the
// group -- and posts everyone's answers back after a timer, or sooner
// if someone replies "reveal" to the announcement (no "chessbot" prefix
// needed there -- replying directly to the bot's own message is already
// specific enough).

const puzzleState = loadPuzzleState();
let revealTimer = null;

// Falls back to this when "chessbot puzzle" doesn't specify "time:N"
// itself. Overridable so a round can be tested without waiting a real
// hour out; with per-round "time:N" now available, testing can just use
// that instead (e.g. "chessbot puzzle time:1") rather than this env var.
const DEFAULT_ROUND_DURATION_MS = Number(process.env.PUZZLE_ROUND_MS) || 60 * 60 * 1000;

const MIN_ROUND_MINUTES = 1;
const MAX_ROUND_MINUTES = 24 * 60;

// Reads "time:N" (minutes) out of the trigger message/caption, anywhere
// in it. Missing, malformed, or out-of-sane-bounds (a typo like
// "time:300" meant as "time:30" shouldn't quietly start a 5-hour round)
// all fall back to the default rather than erroring -- "chessbot puzzle"
// alone should always just work.
function parseRoundDurationMs(text) {

    const match = text.match(/time:\s*(\d+)/i);

    if (!match) {
        return DEFAULT_ROUND_DURATION_MS;
    }

    const minutes = Number(match[1]);

    if (minutes < MIN_ROUND_MINUTES || minutes > MAX_ROUND_MINUTES) {
        return DEFAULT_ROUND_DURATION_MS;
    }

    return minutes * 60 * 1000;
}

function puzzleAnnouncement(durationMs) {

    const minutes = Math.round(durationMs / 60000);

    return `Puzzle round active! DM me your answer — don't post it here. ` +
        `In ${minutes} minutes I'll share what everyone submitted in this chat. ` +
        `To reveal early, reply "reveal" to this message.`;
}

function formatTimeRemaining(ms) {

    if (ms <= 0) {
        return 'less than a minute';
    }

    const minutes = Math.ceil(ms / 60000);

    return minutes === 1 ? '1 minute' : `${minutes} minutes`;
}

// Deliberately no answer content or names here -- the whole point of
// DMing answers privately is that they stay private until reveal.
function puzzleStatusText(round) {

    const answerCount = Object.keys(round.answers).length;
    const remaining = formatTimeRemaining(round.deadline - Date.now());

    return (
        `${answerCount} answer${answerCount === 1 ? '' : 's'} so far. ` +
        `${remaining} left before I reveal (or reply "reveal" to the ` +
        'puzzle message in the group to do it sooner).'
    );
}

// Called on every startBot() (fresh start and every reconnect) so a
// persisted in-progress round keeps counting down correctly, and so a
// reconnect re-points the pending reveal at the current, live socket.
function scheduleReveal(sock) {

    if (revealTimer) {
        clearTimeout(revealTimer);
        revealTimer = null;
    }

    if (!puzzleState.round) {
        return;
    }

    const msRemaining = puzzleState.round.deadline - Date.now();

    if (msRemaining <= 0) {
        // The deadline already passed while the bot was down -- reveal
        // now instead of waiting for a startTimeout that would never
        // have fired.
        revealPuzzle(sock);
        return;
    }

    revealTimer = setTimeout(() => revealPuzzle(sock), msRemaining);
}

async function revealPuzzle(sock) {

    if (!puzzleState.round) {
        return;
    }

    if (revealTimer) {
        clearTimeout(revealTimer);
        revealTimer = null;
    }

    const { groupId, answers, imagePath } = puzzleState.round;
    const entries = Object.entries(answers);

    const text = entries.length === 0
        ? 'Nobody answered this one.'
        : 'Puzzle answers:\n' + entries
            .map(([jid, answer]) => `${puzzleState.names[jid] || 'Unknown'}: ${answer}`)
            .join('\n');

    // Clear the round before sending -- if the send fails, we'd rather
    // lose the reveal than get stuck re-revealing a stale round forever.
    puzzleState.round = null;
    savePuzzleState(puzzleState);

    if (imagePath && fs.existsSync(imagePath)) {
        fs.unlinkSync(imagePath);
    }

    try {
        await sock.sendMessage(groupId, { text });
        console.log(`Puzzle revealed in ${groupId}:\n${text}`);
    } catch (error) {
        console.error('Could not post puzzle answers:', error.message || error);
    }
}

async function handlePuzzleStart(sock, message, groupId, contextInfo, triggerText) {

    if (puzzleState.round) {
        console.log(`Puzzle: rejected new round in ${groupId} -- one's already active.`);
        await sock.sendMessage(groupId, {
            text:
                'A puzzle is already running. Reply "reveal" to ' +
                "that puzzle's message to end it early, or wait for it to finish."
        }, { quoted: message });
        return;
    }

    const hasAttachedImage = !!message.message?.imageMessage;
    const hasQuotedImage = !!contextInfo?.quotedMessage?.imageMessage;

    if (!hasAttachedImage && !hasQuotedImage) {
        await sock.sendMessage(groupId, {
            text:
                'Post the image with "chessbot puzzle" (or "chessbot poll") as ' +
                'the caption -- or reply to it with the same -- to start a round.'
        }, { quoted: message });
        return;
    }

    const imagePath = hasAttachedImage
        ? await downloadDirectImage(message, groupId)
        : await downloadQuotedImage(contextInfo, groupId);

    if (!imagePath) {
        await sock.sendMessage(groupId, {
            text:
                "I couldn't download that image, try resending it " +
                "and replying to the new one."
        }, { quoted: message });
        return;
    }

    const durationMs = parseRoundDurationMs(triggerText);

    // Kept on disk (unlike an analysis image) for the life of the round,
    // so it can be re-sent to anyone who DMs asking to see it again --
    // deleted once the round is revealed, in revealPuzzle. Only cleaned
    // up early if the announcement itself never went out -- no round
    // means nothing will ever reference this path.
    let sent;

    try {
        sent = await sock.sendMessage(groupId, {
            image: fs.readFileSync(imagePath),
            caption: puzzleAnnouncement(durationMs)
        });
    } catch (error) {
        if (fs.existsSync(imagePath)) {
            fs.unlinkSync(imagePath);
        }
        throw error;
    }

    puzzleState.round = {
        groupId,
        announcementId: sent.key.id,
        imagePath,
        deadline: Date.now() + durationMs,
        answers: {},
        awaitingName: []
    };
    savePuzzleState(puzzleState);
    scheduleReveal(sock);

    console.log(
        `Puzzle round started in ${groupId}, revealing at ` +
        new Date(puzzleState.round.deadline).toISOString()
    );
}

async function handlePuzzleRevealCommand(sock, groupId, contextInfo) {

    if (!puzzleState.round || puzzleState.round.groupId !== groupId) {
        return;
    }

    // Must reply to the bot's own announcement -- otherwise "reveal"
    // typed anywhere in the group would end the round early.
    if (contextInfo?.stanzaId !== puzzleState.round.announcementId) {
        return;
    }

    console.log(`Puzzle reveal overridden early in ${groupId}.`);
    await revealPuzzle(sock);
}

async function handlePuzzleStatusCommand(sock, message, groupId, contextInfo) {

    if (!puzzleState.round || puzzleState.round.groupId !== groupId) {
        return;
    }

    // Same anchor as the reveal command -- must reply to the bot's own
    // announcement, so "status" typed anywhere in the group doesn't
    // trigger it.
    if (contextInfo?.stanzaId !== puzzleState.round.announcementId) {
        return;
    }

    await sock.sendMessage(groupId, {
        text: puzzleStatusText(puzzleState.round)
    }, { quoted: message });
}

// Direct messages are used for exactly one thing: submitting a puzzle
// answer. First message from a never-seen jid is recorded as their
// answer immediately (so they never need to resend it), and only then
// does the bot ask for their name -- once given, it's remembered for
// every future round too.
async function handlePuzzleDM(sock, message, remoteJid) {

    // Some accounts show up with a privacy-preserving "@lid" address as
    // remoteJid instead of their real number -- confirmed empirically
    // that sending TO an @lid address silently fails to deliver (no
    // error, message just never arrives). senderPn, when present, is
    // the real @s.whatsapp.net JID for the same person; use it as the
    // canonical identity for everything in this function -- storage
    // keys and the reply target both -- whenever it's available.
    const senderJid = message.key.senderPn || remoteJid;

    // Collapsed to one line -- a name or answer with an embedded newline
    // could otherwise be crafted to look like a second, fake entry
    // ("Real Name\nSomeone Else: fake answer") once the reveal message
    // joins everyone's line together.
    const text = (
        message.message.conversation ||
        message.message.extendedTextMessage?.text ||
        ''
    ).replace(/\s*\n\s*/g, ' ').trim();

    if (!text) {
        return;
    }

    // "name: X" sets/changes the stored name at any time -- first
    // contact or not, mid-round or not -- so a typo'd or joke name can
    // be fixed later. Takes priority over everything else, including
    // an in-progress "what's your name?" follow-up (and clears that
    // follow-up if one was pending, so someone who jumps straight to
    // "name: X" doesn't get stuck still "awaiting" a name).
    const nameChangeMatch = text.match(/^\s*name:\s*(.+)/i);

    if (nameChangeMatch) {
        const newName = nameChangeMatch[1].trim();
        const oldName = puzzleState.names[senderJid];
        puzzleState.names[senderJid] = newName;

        if (puzzleState.round) {
            puzzleState.round.awaitingName =
                puzzleState.round.awaitingName.filter((jid) => jid !== senderJid);
        }

        savePuzzleState(puzzleState);

        console.log(
            `Puzzle: ${senderJid} set their name to "${newName}"` +
            (oldName ? ` (was "${oldName}")` : '')
        );

        await sock.sendMessage(senderJid, {
            text: `Got it, you're "${newName}" now.`
        });
        return;
    }

    // A casual question about renaming gets pointed at the command
    // above, instead of being recorded as a puzzle answer or taken
    // literally as a new name.
    const lowerText = text.toLowerCase();
    const isNameHelpQuery =
        lowerText.includes('name') &&
        (lowerText.includes('change') || lowerText.includes('update') || lowerText.includes('rename'));

    if (isNameHelpQuery) {
        await sock.sendMessage(senderJid, {
            text: 'DM me "name: YourName" (e.g. "name: Braxton") any time to set or change your name.'
        });
        return;
    }

    if (!puzzleState.round) {
        console.log(`Puzzle: DM from ${senderJid} but no round active, replying.`);
        await sock.sendMessage(senderJid, {
            text: 'No puzzle running right now.'
        });
        return;
    }

    // Status and image requests are checked before awaitingName/answer
    // handling so they work at any point mid-round -- including for
    // someone who's already answered and is still being asked their
    // name -- without getting misrecorded as an answer or a name.
    if (lowerText.includes('status')) {
        await sock.sendMessage(senderJid, { text: puzzleStatusText(puzzleState.round) });
        return;
    }

    if (
        lowerText.includes('image') ||
        lowerText.includes('picture') ||
        lowerText.includes('photo') ||
        lowerText.includes('show puzzle') ||
        lowerText.includes('show position')
    ) {
        const { imagePath } = puzzleState.round;

        if (imagePath && fs.existsSync(imagePath)) {
            await sock.sendMessage(senderJid, {
                image: fs.readFileSync(imagePath),
                caption: 'Here\'s the current puzzle.'
            });
        } else {
            await sock.sendMessage(senderJid, {
                text: "I don't have that image anymore, sorry."
            });
        }

        return;
    }

    if (puzzleState.round.awaitingName.includes(senderJid)) {
        puzzleState.names[senderJid] = text;
        puzzleState.round.awaitingName =
            puzzleState.round.awaitingName.filter((jid) => jid !== senderJid);
        savePuzzleState(puzzleState);

        console.log(`Puzzle: ${senderJid} gave their name: ${text}`);

        await sock.sendMessage(senderJid, {
            text: `Thanks, ${text}! Your answer's in.`
        });
        return;
    }

    const isNewAnswer = !(senderJid in puzzleState.round.answers);
    puzzleState.round.answers[senderJid] = text;

    if (!(senderJid in puzzleState.names)) {
        puzzleState.round.awaitingName.push(senderJid);
        savePuzzleState(puzzleState);

        console.log(`Puzzle: new answer from unnamed ${senderJid}: ${text}`);

        await sock.sendMessage(senderJid, {
            text:
                "Got it! First time hearing from you -- what's your name, " +
                "so I can label your answer?"
        });
        return;
    }

    savePuzzleState(puzzleState);

    console.log(
        `Puzzle: ${isNewAnswer ? 'new' : 'updated'} answer from ` +
        `${puzzleState.names[senderJid]} (${senderJid}): ${text}`
    );

    await sock.sendMessage(senderJid, {
        text: isNewAnswer ? 'Answer recorded!' : 'Got it, updated your answer.'
    });
}


// ============================================================
// HANDLE INCOMING MESSAGES
// ============================================================

async function handleMessage(sock, message) {

    if (!message.message || message.key.fromMe) {
        return;
    }

    const remoteJid = message.key.remoteJid;

    if (!remoteJid) {
        return;
    }

    if (!remoteJid.endsWith('@g.us')) {

        if (remoteJid === 'status@broadcast') {
            return;
        }

        // Not a group chat -- DMs are only ever used for puzzle answers.
        try {
            await handlePuzzleDM(sock, message, remoteJid);
        } catch (error) {
            console.error('Error handling puzzle DM:', error.message || error);
        }
        return;
    }

    const groupId = remoteJid;


    // ====================================================
    // 1. CHECK FOR CHESSBOT COMMAND
    // ====================================================

    const text = (
        message.message.conversation ||
        message.message.extendedTextMessage?.text ||
        ''
    ).toLowerCase().trim();

    // A puzzle image's own caption -- lets "chessbot puzzle" arrive
    // together with the image in one message, not just as a separate
    // reply to it.
    const captionText = (message.message.imageMessage?.caption || '').toLowerCase().trim();

    const contextInfo = message.message.extendedTextMessage?.contextInfo;

    // "chessbot poll" is the same trigger under a different name -- the
    // mechanism (compile everyone's private answers, reveal together)
    // isn't specific to chess puzzles.
    const isPuzzleStartTrigger = (t) => t.includes('chessbot puzzle') || t.includes('chessbot poll');

    if (isPuzzleStartTrigger(text) || isPuzzleStartTrigger(captionText)) {
        const triggerText = isPuzzleStartTrigger(captionText) ? captionText : text;
        try {
            await handlePuzzleStart(sock, message, groupId, contextInfo, triggerText);
        } catch (error) {
            console.error('Error starting puzzle round:', error.message || error);
        }
        return;
    }

    if (text.includes('reveal')) {
        try {
            await handlePuzzleRevealCommand(sock, groupId, contextInfo);
        } catch (error) {
            console.error('Error revealing puzzle round:', error.message || error);
        }
        return;
    }

    if (text.includes('status')) {
        try {
            await handlePuzzleStatusCommand(sock, message, groupId, contextInfo);
        } catch (error) {
            console.error('Error replying to puzzle status:', error.message || error);
        }
        return;
    }

    const tokens = text.split(/\s+/).filter(Boolean);

    // Several equivalent ways to trigger the same thing:
    //   fen
    //   chessbot link
    //   chessbot send link
    // "fen" must be the first word (avoids false triggers in normal
    // chat); the "chessbot ..." phrases are matched anywhere in the
    // message since they're distinctive enough not to false-trigger.
    const isFenTrigger = tokens[0] === 'fen';
    const isChessbotTrigger =
        text.includes('chessbot link') ||
        text.includes('chessbot send link');

    if (!isFenTrigger && !isChessbotTrigger) {
        return;
    }

    console.log('ChessBot command received.');

    // Several equivalent ways to flag black to play:
    //   fen b / chessbot link b / chessbot send link b
    //   ... btp
    //   ... black
    // Matched as a whole token so "b"/"btp" don't accidentally match
    // inside other words. Anything else (or nothing) defaults to white.
    const blackFlags = ['b', 'btp', 'black'];
    const sideToMove = tokens.some((t) => blackFlags.includes(t)) ? 'b' : 'w';

    console.log(`Side to move: ${sideToMove === 'b' ? 'black' : 'white (default)'}`);

    let imagePath = null;

    try {

        // ====================================================
        // 2. REQUIRE A REPLY TO THE IMAGE BEING ANALYZED
        // ====================================================

        if (!contextInfo?.quotedMessage?.imageMessage) {

            await sock.sendMessage(groupId, {
                text:
                    'Reply directly to the chessboard image with ' +
                    '"fen" (or "chessbot link" / "chessbot send link") ' +
                    'so I know exactly which photo to analyze. ' +
                    'Add "b" / "btp" / "black" for black to play.'
            }, { quoted: message });

            return;
        }


        // ====================================================
        // 3. DOWNLOAD THE QUOTED IMAGE
        // ====================================================

        imagePath = await downloadQuotedImage(contextInfo, groupId);

        if (!imagePath) {

            await sock.sendMessage(groupId, {
                text:
                    "I couldn't download that image, try resending it " +
                    "and replying to the new one."
            }, { quoted: message });

            return;
        }


        // ====================================================
        // 4. ANALYZE POSITION
        // ====================================================

        const lichessUrl = await analyzeImage(imagePath, sideToMove);

        console.log(`Lichess URL: ${lichessUrl}`);


        // ====================================================
        // 5. SEND RESULT
        // ====================================================

        await sock.sendMessage(groupId, {
            text: `Here's the link: ${lichessUrl}`
        }, { quoted: message });

    } catch (error) {

        console.error('ChessBot error:', error);

        // Python's own errors (crop failure, etc.) already carry a
        // useful, user-facing explanation -- forward that instead of
        // always sending the same generic apology. Anything else
        // (unexpected JS-side errors, timeouts) still gets a safe
        // generic message rather than leaking internals to the group.
        const rawMessage = error?.message || '';
        let userMessage = "Sorry, I couldn't analyze that position.";

        if (rawMessage.startsWith('ERROR:')) {
            userMessage = rawMessage.replace(/^ERROR:\s*/, '').trim();
        } else if (rawMessage === 'Analysis timed out.') {
            userMessage = 'That took too long to analyze. Try again in a moment.';
        } else if (rawMessage === 'Chess model is not ready yet.') {
            // Loading the model can take well over a minute on slower
            // hardware -- without this, a request that lands in that
            // window gets the same generic apology as a genuinely bad
            // photo, which reads as "this is broken" instead of "wait a
            // few seconds."
            userMessage = "I'm still starting up, give me a few seconds and try again.";
        }

        try {
            await sock.sendMessage(groupId, { text: userMessage });
        } catch (replyError) {
            console.error('Could not send error message:', replyError);
        }

    } finally {

        // Every request downloads its own fresh image and never needs
        // it again afterward. Clean up the original plus anything
        // crop_board.py derived from it, so disk usage doesn't grow
        // unbounded the longer this runs.
        if (imagePath) {

            const ext = path.extname(imagePath);
            const base = imagePath.slice(0, -ext.length);

            const filesToRemove = [
                imagePath,
                `${base}_cropped${ext}`,
                `${base}_nearmiss${ext}`
            ];

            for (const filePath of filesToRemove) {
                try {
                    if (fs.existsSync(filePath)) {
                        fs.unlinkSync(filePath);
                    }
                } catch (cleanupError) {
                    console.error(
                        `Could not delete ${filePath}:`,
                        cleanupError.message || cleanupError
                    );
                }
            }
        }
    }
}


// ============================================================
// WHATSAPP CONNECTION
// ============================================================

// Baileys 7's retry protocol needs the original content of a message
// back if the recipient's device couldn't decrypt it the first try --
// without a way to supply that, a message can get silently stuck
// (this was the root cause of DM replies going out but never arriving:
// staying at "PENDING" forever, with no delivery and no error). This
// caches what we've sent, by message id, so getMessage can serve it
// back on request. Capped so a long-running process doesn't grow this
// unbounded -- only recent messages are ever plausible retry targets.
const sentMessageCache = new Map();
const SENT_MESSAGE_CACHE_LIMIT = 1000;

async function getMessage(key) {
    return sentMessageCache.get(key.id);
}

async function startBot() {

    // Baileys session state lives in ./session as plain JSON files.
    // This is a different format from whatsapp-web.js's Puppeteer
    // profile folder, so the first run needs a fresh QR scan.
    const { state, saveCreds } = await useMultiFileAuthState(
        path.join(__dirname, 'session')
    );

    // The version baked into a pinned Baileys release goes stale as
    // WhatsApp updates its web client. Fetching the current one avoids
    // an immediate "Connection Failure" (405) before the QR ever shows.
    const { version, isLatest } = await fetchLatestBaileysVersion();
    console.log(`Using WA v${version.join('.')}, isLatest: ${isLatest}`);

    const sock = makeWASocket({
        auth: state,
        version,
        logger: P({ level: 'silent' }),
        printQRInTerminal: false,
        getMessage
    });

    // Every send gets cached (by wrapping here, instead of touching
    // every individual sendMessage call site) so getMessage above can
    // always serve back whatever was actually sent most recently.
    const rawSendMessage = sock.sendMessage.bind(sock);

    sock.sendMessage = async (...args) => {
        const result = await rawSendMessage(...args);

        if (result?.key?.id && result?.message) {
            sentMessageCache.set(result.key.id, result.message);

            if (sentMessageCache.size > SENT_MESSAGE_CACHE_LIMIT) {
                sentMessageCache.delete(sentMessageCache.keys().next().value);
            }
        }

        return result;
    };

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {

        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            console.log('Scan this QR code with the ChessBot WhatsApp account:');
            qrcode.generate(qr, { small: true });
        }

        if (connection === 'open') {
            console.log('ChessBot WhatsApp client is ready!');

            // Resumes a puzzle round that was still running when the bot
            // last stopped, and re-points a still-running round's reveal
            // at this (possibly reconnected) socket.
            scheduleReveal(sock);
        }

        if (connection === 'close') {

            const statusCode = new Boom(lastDisconnect?.error)?.output?.statusCode;
            const loggedOut = statusCode === DisconnectReason.loggedOut;

            console.error('Connection closed:', lastDisconnect?.error?.message);

            if (!loggedOut) {
                console.log('Reconnecting...');
                startBot();
            } else {
                console.log('Logged out. Delete the ./session folder and re-run to scan a new QR code.');
            }
        }
    });

    sock.ev.on('messages.upsert', async ({ messages, type }) => {

        if (type !== 'notify') {
            return;
        }

        for (const message of messages) {
            await handleMessage(sock, message);
        }
    });
}

startBot();
