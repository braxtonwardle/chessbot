const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

const client = new Client({
    authStrategy: new LocalAuth()
});

client.on('qr', (qr) => {
    console.log('Scan this QR code with WhatsApp:');
    qrcode.generate(qr, { small: true });
});

client.on('authenticated', () => {
    console.log('WhatsApp authenticated.');
});

client.on('auth_failure', (message) => {
    console.error('Authentication failed:', message);
});

client.on('ready', async () => {
    console.log('ChessBot WhatsApp client is ready!');

    const myNumber = client.info.wid._serialized;

    await client.sendMessage(
        myNumber,
        'Hello from ChessBot! ♟️'
    );
});

client.initialize();