const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');


// ============================================================
// WHATSAPP
// ============================================================

const client = new Client({
    authStrategy: new LocalAuth({
        clientId: 'chessbot'
    })
});

client.on('qr', (qr) => {
    console.log('Scan this QR code with the ChessBot WhatsApp account:');
    qrcode.generate(qr, { small: true });
});

client.on('authenticated', () => {
    console.log('WhatsApp authenticated.');
});

client.on('auth_failure', (message) => {
    console.error('WhatsApp authentication failed:', message);
});


// ============================================================
// PYTHON CHESS ENGINE
// ============================================================

console.log('Starting chess engine...');

const python = spawn('python', ['chessbot.py']);

let pythonReady = false;
let stdoutBuffer = '';

python.stderr.on('data', (data) => {
    const message = data.toString().trim();

    if (!message) {
        return;
    }

    console.log(`Python: ${message}`);

    if (message === 'READY') {
        pythonReady = true;
        console.log('Chess model is ready.');
    }
});

python.stdout.on('data', (data) => {
    stdoutBuffer += data.toString();

    const lines = stdoutBuffer.split('\n');
    stdoutBuffer = lines.pop();

    for (const line of lines) {
        const result = line.trim();

        if (result) {
            console.log(`Python result: ${result}`);
        }
    }
});

python.on('close', (code) => {
    console.log(`Python exited with code ${code}`);
});


// ============================================================
// IMAGE STORAGE
// ============================================================

const latestImages = new Map();


// ============================================================
// SEND IMAGE TO PYTHON
// ============================================================

function analyzeImage(imagePath) {

    return new Promise((resolve, reject) => {

        if (!pythonReady) {
            reject(new Error('Chess model is not ready yet.'));
            return;
        }

        let resultBuffer = '';

        const onData = (data) => {

            resultBuffer += data.toString();

            const lines = resultBuffer.split('\n');
            resultBuffer = lines.pop();

            for (const line of lines) {

                const result = line.trim();

                if (!result) {
                    continue;
                }

                python.stdout.off('data', onData);

                if (result.startsWith('ERROR:')) {
                    reject(new Error(result));
                } else {
                    resolve(result);
                }

                return;
            }
        };

        python.stdout.on('data', onData);

        python.stdin.write(`${imagePath}\n`);
    });
}


// ============================================================
// SAVE INCOMING IMAGE (isolated from the analysis flow)
// ============================================================

// Returns true if an image was saved, false if this message had no
// image, and does NOT throw on a failed download — a broken
// downloadMedia() should never surface as an "analysis failed"
// reply on a message nobody asked ChessBot to look at.
async function trySaveIncomingImage(message) {

    if (!(message.hasMedia && message.type === 'image')) {
        return false;
    }

    console.log('Media message received.');
    console.log('MIME type:', message._data?.mimetype);

    let media;

    try {
        media = await message.downloadMedia();
    } catch (downloadError) {
        console.error(
            'Image download failed (downloadMedia threw):',
            downloadError.message || downloadError
        );
        return false;
    }

    if (!media || !media.data) {
        console.error('Image download returned no data.');
        return false;
    }

    const extension =
        media.mimetype.split('/')[1] || 'jpg';

    const safeGroupId =
        message.from.replace(/[^a-zA-Z0-9]/g, '');

    const imagePath = path.join(
        __dirname,
        `received_${safeGroupId}.${extension}`
    );

    fs.writeFileSync(
        imagePath,
        Buffer.from(media.data, 'base64')
    );

    latestImages.set(message.from, imagePath);

    console.log(`Saved latest group image: ${imagePath}`);

    return true;
}


// ============================================================
// WHATSAPP READY
// ============================================================

client.on('ready', () => {
    console.log('ChessBot WhatsApp client is ready!');
});


// ============================================================
// HANDLE INCOMING MESSAGES
// ============================================================

client.on('message', async (message) => {

    // Only process group messages
    if (!message.from.endsWith('@g.us')) {
        return;
    }


    // ====================================================
    // 1. SAVE THE MOST RECENT IMAGE (never throws)
    // ====================================================

    await trySaveIncomingImage(message);


    // ====================================================
    // 2. CHECK FOR CHESSBOT COMMAND
    // ====================================================

    const text = message.body
        .toLowerCase()
        .trim();

    if (
        !text.includes('chessbot') ||
        !text.includes('send link')
    ) {
        return;
    }

    console.log('ChessBot command received.');

    try {

        // ====================================================
        // 3. FIND SAVED IMAGE
        // ====================================================

        const imagePath = latestImages.get(message.from);

        if (!imagePath || !fs.existsSync(imagePath)) {

            await message.reply(
                "I couldn't find a recent image to analyze. " +
                "If you just sent one, WhatsApp's image download " +
                "may have failed on this bot's end — try resending it."
            );

            return;
        }

        console.log(
            `Using saved image: ${imagePath}`
        );


        // ====================================================
        // 4. ANALYZE POSITION
        // ====================================================

        await message.reply(
            'Got it — analyzing the position... ♟️'
        );

        const lichessUrl = await analyzeImage(imagePath);

        console.log(
            `Lichess URL: ${lichessUrl}`
        );


        // ====================================================
        // 5. SEND RESULT
        // ====================================================

        await message.reply(
            `Here's the position:\n${lichessUrl}`
        );

    } catch (error) {

        console.error('ChessBot error:', error);

        try {
            await message.reply(
                "Sorry, I couldn't analyze that position."
            );
        } catch (replyError) {
            console.error(
                'Could not send error message:',
                replyError
            );
        }
    }
});


// ============================================================
// START
// ============================================================

client.initialize();