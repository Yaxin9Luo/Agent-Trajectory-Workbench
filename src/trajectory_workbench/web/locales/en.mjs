// English interface text: every per-file table merged into one. Two tables must not give the same
// key different translations (tests/test_i18n.mjs checks).
import rewrites from "./en/rewrites.mjs";
import rewrite_batch from "./en/rewrite-batch.mjs";
import reader from "./en/reader.mjs";
import jev from "./en/jev.mjs";
import ledgers from "./en/ledgers.mjs";
import moh from "./en/moh.mjs";
import library from "./en/library.mjs";
import stats from "./en/stats.mjs";
import pair from "./en/pair.mjs";
import matrix from "./en/matrix.mjs";
import queue from "./en/queue.mjs";
import explore from "./en/explore.mjs";
import review from "./en/review.mjs";
import v4reward from "./en/v4reward.mjs";
import annotations from "./en/annotations.mjs";
import search from "./en/search.mjs";
import presentation from "./en/presentation.mjs";
import app from "./en/app.mjs";
import replay from "./en/replay.mjs";
import backend from "./en/backend.mjs";

export const TABLES = { rewrites, "rewrite-batch": rewrite_batch, reader, jev, ledgers, moh, library, stats, pair, matrix, queue, explore, review, v4reward, annotations, search, presentation, app, replay, backend };

export default Object.assign({}, ...Object.values(TABLES));
