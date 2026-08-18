// puzzleState.mjs
//
// Persists puzzle-round state (and the jid -> name directory) to disk so
// a bot restart mid-round doesn't lose collected answers or make everyone
// re-introduce themselves. Pure storage shape and load/save mechanics --
// chessbot.mjs owns the actual round lifecycle and WhatsApp glue.
//
// Shape:
//   {
//     names: { [jid]: name },   // remembered across every round, forever
//     round: null | {
//       groupId,
//       announcementId,   // message id of the bot's own puzzle post, so a
//                          // "chessbot reveal" reply can be matched to it
//       deadline,          // epoch ms
//       answers: { [jid]: answerText },
//       awaitingName: [jid, ...]   // answered this round, but we don't
//                                  // have a name for them yet
//     }
//   }

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STATE_FILE = path.join(__dirname, 'puzzle_state.json');

function defaultState() {
    return {
        names: {},
        round: null
    };
}

export function loadPuzzleState() {
    if (!fs.existsSync(STATE_FILE)) {
        return defaultState();
    }

    try {
        return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    } catch (error) {
        console.error(
            `Could not read ${STATE_FILE}, starting fresh:`,
            error.message || error
        );
        return defaultState();
    }
}

export function savePuzzleState(state) {
    fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
}
