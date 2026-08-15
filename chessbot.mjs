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

const __dirname = path.dirname(fileURLToPath(import.meta.url));


// ============================================================
// PYTHON CHESS ENGINE  (unchanged from the whatsapp-web.js version)
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
// DOWNLOAD A QUOTED (REPLIED-TO) IMAGE
// ============================================================

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

    const mimetype = quotedImageMessage.mimetype || 'image/jpeg';
    const extension = mimetype.split('/')[1] || 'jpg';
    const safeGroupId = groupId.replace(/[^a-zA-Z0-9]/g, '');
    // Timestamped filename -- each analysis request downloads its own
    // image fresh, so nothing can be silently overwritten by unrelated
    // messages the way a single per-group "latest image" slot could be.
    const imagePath = path.join(
        __dirname,
        `received_${safeGroupId}_${Date.now()}.${extension}`
    );

    fs.writeFileSync(imagePath, buffer);

    console.log(`Saved replied-to image: ${imagePath}`);

    return imagePath;
}


// ============================================================
// HANDLE INCOMING MESSAGES
// ============================================================

async function handleMessage(sock, message) {

    if (!message.message || message.key.fromMe) {
        return;
    }

    const groupId = message.key.remoteJid;

    // Only process group messages
    if (!groupId || !groupId.endsWith('@g.us')) {
        return;
    }


    // ====================================================
    // 1. CHECK FOR CHESSBOT COMMAND
    // ====================================================

    const text = (
        message.message.conversation ||
        message.message.extendedTextMessage?.text ||
        ''
    ).toLowerCase().trim();

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

        const contextInfo = message.message.extendedTextMessage?.contextInfo;

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

        await sock.sendMessage(groupId, {
            text: 'Got it — analyzing the position... ♟️'
        }, { quoted: message });

        const lichessUrl = await analyzeImage(imagePath, sideToMove);

        console.log(`Lichess URL: ${lichessUrl}`);


        // ====================================================
        // 5. SEND RESULT
        // ====================================================

        await sock.sendMessage(groupId, {
            text: lichessUrl
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
        printQRInTerminal: false
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {

        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            console.log('Scan this QR code with the ChessBot WhatsApp account:');
            qrcode.generate(qr, { small: true });
        }

        if (connection === 'open') {
            console.log('ChessBot WhatsApp client is ready!');
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
