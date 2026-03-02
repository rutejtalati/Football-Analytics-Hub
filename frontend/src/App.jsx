// src/App.jsx
import { useEffect, useMemo, useRef, useState } from "react";
import { DndContext, useDraggable, useDroppable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import "./styles.css";

/**
 * IMPORTANT FIXES INCLUDED:
 * 1) GK missing: normalize positions so GK can be "GK" or "GKP" (and also supports FPL element_type=1 if present)
 * 2) Best Team nav button “unclickable”: ensure header/nav buttons are always clickable (stop accidental overlays)
 *    - also allow opening Best Team page even if fetch fails, so UI still responds
 * 3) Manual squad builder overlap: switch manual builder to VERTICAL sections (GK/DEF/MID/FWD stacked)
 * 4) Pitch view: smaller “player card” layout with ShirtIcon kept (Option 2 shirts)
 * 5) Best XI constraints: 11 players only, always 1 GK, at least 3 DEF, at least 3 MID, at least 1 FWD,
 *    with limits DEF<=5, MID<=5, FWD<=3. Picks optimized using table values (GW points).
 */

const TEAM_STYLES = {
  ARS: { a: "#d40000", b: "#d4af37" },
  MCI: { a: "#7ec8ff", b: "#ffffff" },
  MUN: { a: "#7a0000", b: "#ffd000" },
  AVL: { a: "#7ec8ff", b: "#ffd000" },
  LIV: { a: "#d40000", b: "#d40000" },
  CHE: { a: "#003cff", b: "#003cff" },
  BRE: { a: "#ffffff", b: "#d40000" },
  EVE: { a: "#003cff", b: "#ffffff" },
  FUL: { a: "#000000", b: "#ffffff" },
  BOU: { a: "#d40000", b: "#000000" },
  BHA: { a: "#ffffff", b: "#003cff" },
  SUN: { a: "#ffffff", b: "#d40000" },
  NEW: { a: "#ffffff", b: "#000000" },
  CRY: { a: "#d40000", b: "#003cff" },
  LEE: { a: "#ffd000", b: "#ffffff" },
  TOT: { a: "#003cff", b: "#ffffff" },
  NFO: { a: "#d40000", b: "#000000" },
  WHU: { a: "#6b3b2a", b: "#1b4fa0" },
  BUR: { a: "#6b3b2a", b: "#ffffff" },
  WOL: { a: "#ffd000", b: "#000000" },
};

const POS_FILTER = ["ALL", "GK", "DEF", "MID", "FWD"];

// Best XI constraints (your requirement)
const MIN_XI = { GK: 1, DEF: 3, MID: 3, FWD: 1 };
const MAX_XI = { DEF: 5, MID: 5, FWD: 3 };

// Manual 15-man squad constraints
const MANUAL_COUNTS = { GK: 2, DEF: 5, MID: 5, FWD: 3 };
const MANUAL_STORAGE_KEY = "fpl_manual_squad_v1";

// ---------- utils ----------
const n = (v, d = 0) => {
  const x = Number(v);
  return Number.isFinite(x) ? x : d;
};
const intPts = (x) => (Number.isFinite(Number(x)) ? Math.round(Number(x)) : 0);

const toNumOrNull = (v) => {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const x = Number(v.trim());
    return Number.isFinite(x) ? x : null;
  }
  return null;
};

const parsePercentFlexible = (v) => {
  if (v == null) return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  const t = String(v).trim();
  if (!t) return null;
  const cleaned = t.endsWith("%") ? t.slice(0, -1).trim() : t;
  const x = Number(cleaned);
  return Number.isFinite(x) ? x : null;
};

function normalizePosition(p) {
  // Position may be: "GK", "GKP", "Goalkeeper", numeric element_type, etc.
  const raw = p?.position ?? p?.pos ?? "";
  const s = String(raw).trim().toUpperCase();

  // direct
  if (s === "GK" || s === "GKP" || s === "GOALKEEPER") return "GK";
  if (s === "DEF" || s === "DEFENDER") return "DEF";
  if (s === "MID" || s === "MIDFIELDER") return "MID";
  if (s === "FWD" || s === "FWR" || s === "FORWARD" || s === "ATT" || s === "STR") return "FWD";

  // FPL element_type (if backend ever sends it)
  const et = p?.element_type;
  if (et === 1 || et === "1") return "GK";
  if (et === 2 || et === "2") return "DEF";
  if (et === 3 || et === "3") return "MID";
  if (et === 4 || et === "4") return "FWD";

  // heuristics
  if (s.includes("GK")) return "GK";
  if (s.includes("DEF")) return "DEF";
  if (s.includes("MID")) return "MID";
  if (s.includes("FWD") || s.includes("FORW") || s.includes("ATT")) return "FWD";

  return s || "UNK";
}

function withNormPos(p) {
  return { ...p, position_norm: normalizePosition(p) };
}

const teamBg = (team) => {
  const t = TEAM_STYLES[team] || { a: "#444", b: "#222" };
  if (t.a === t.b) return t.a;
  return `linear-gradient(90deg, ${t.a} 0 50%, ${t.b} 50% 100%)`;
};

const teamTextColor = (team) => {
  const t = TEAM_STYLES[team] || { a: "#444", b: "#222" };
  const hasBlack = [t.a, t.b].some((c) => c.toLowerCase() === "#000000");
  const hasWhite = [t.a, t.b].some((c) => c.toLowerCase() === "#ffffff");
  if (hasBlack && hasWhite) return "#ff3b3b";
  if (hasBlack) return "#fff";
  return "#000";
};

const clamp01 = (x) => Math.max(0, Math.min(1, x));
const lerp = (a, b, t) => Math.round(a + (b - a) * t);

function heatRedGreen(v, min, max, darkLow = false) {
  if (!Number.isFinite(v) || max <= min) return "transparent";
  const t = clamp01((v - min) / (max - min));
  const low = darkLow ? [90, 22, 22] : [150, 40, 40];
  const mid = [155, 125, 55];
  const high = [35, 165, 95];
  const [a, b, tt] = t < 0.5 ? [low, mid, t / 0.5] : [mid, high, (t - 0.5) / 0.5];
  return `rgba(${lerp(a[0], b[0], tt)}, ${lerp(a[1], b[1], tt)}, ${lerp(a[2], b[2], tt)}, 0.45)`;
}

function heatLegacy(v, min, max) {
  if (!Number.isFinite(v) || max <= min) return "transparent";
  const t = clamp01((v - min) / (max - min));
  const stops = [
    [210, 0, 0],
    [170, 80, 220],
    [90, 170, 255],
    [0, 200, 90],
  ];
  const seg =
    t < 0.35 ? [0, 1, t / 0.35] : t < 0.65 ? [1, 2, (t - 0.35) / 0.3] : [2, 3, (t - 0.65) / 0.35];
  const a = stops[seg[0]],
    b = stops[seg[1]],
    tt = seg[2];
  return `rgb(${lerp(a[0], b[0], tt)}, ${lerp(a[1], b[1], tt)}, ${lerp(a[2], b[2], tt)})`;
}

const probCls = (p) => (p < 0.5 ? "prob-low" : p < 0.75 ? "prob-mid" : p < 0.9 ? "prob-good" : "prob-great");
const availCls = (pct) => (pct == null ? "avail-low" : pct >= 100 ? "avail-100" : pct < 50 ? "avail-low" : "avail-mid");
const gwCls = (x) => (x < 3 ? "gw-low" : x <= 5 ? "gw-mid" : "gw-high");
const oppFdrCls = (fdr) => (!Number.isFinite(fdr) ? "" : fdr <= 2.2 ? "opp-fdr-good" : fdr <= 3.2 ? "opp-fdr-mid" : "opp-fdr-bad");
const TEAM_CAP = 3;
const LEAGUE_CARDS = [
  { code: "PL", name: "EPL", subtitle: "Premier League" },
  { code: "PD", name: "La Liga", subtitle: "Spain" },
  { code: "SA", name: "Serie A", subtitle: "Italy" },
  { code: "FL1", name: "Ligue 1", subtitle: "France" },
];

const APP_TITLE_OPTIONS = [
  "Football Forecast Lab",
  "Match Predictor Dashboard",
  "Football Analytics Hub",
  "Data-Driven Football Predictions",
  "Football Insights & Projections",
  "Matchday Model: Predictions & Tools",
  "Football Intelligence Console",
  "Football Stats & Forecasts",
];
const APP_TITLE = "Football Analytics Hub";

function compareValues(a, b, key) {
  if (key === "chance_play_pct") {
    const an = parsePercentFlexible(a);
    const bn = parsePercentFlexible(b);
    if (an == null && bn == null) return 0;
    if (an == null) return 1;
    if (bn == null) return -1;
    return an === bn ? 0 : an < bn ? -1 : 1;
  }
  const an = toNumOrNull(a),
    bn = toNumOrNull(b);
  if (an != null && bn != null) return an === bn ? 0 : an < bn ? -1 : 1;
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return String(a).toLowerCase().localeCompare(String(b).toLowerCase());
}

function oppDifficultyClass(team, oppTeam, ranks) {
  if (!ranks || !team || !oppTeam) return "";
  const tr = Number(ranks[team]);
  const or = Number(ranks[oppTeam]);
  if (!Number.isFinite(tr) || !Number.isFinite(or)) return "";
  const delta = tr - or; // positive means opponent is better-ranked
  if (or <= 4 && delta >= 10) return "opp-hard";
  if (delta >= 3 || (or <= 8 && or < tr)) return "opp-med";
  if (Math.abs(delta) <= 2) return "opp-neutral";
  if (delta <= -3) return "opp-easy";
  return "";
}

function normalizeHeatValue(columnKey, rawValue) {
  const v = Number(rawValue);
  if (!Number.isFinite(v)) return null;
  const percentCols = new Set([
    "homeWinPct",
    "drawPct",
    "awayWinPct",
    "over25Pct",
    "bttsPct",
    "homeCsPct",
    "awayCsPct",
    "home2PlusPct",
    "away2PlusPct",
  ]);
  if (percentCols.has(columnKey) && v <= 1.0001) return v * 100;
  return v;
}

function getHeatClass(columnKey, rawValue) {
  const v = normalizeHeatValue(columnKey, rawValue);
  if (v == null) return "";

  const byThreshold = (r, y, g) => {
    if (v < r) return "heat-red";
    if (v <= y) return "heat-yellow";
    if (v <= g) return "heat-grey";
    return "heat-green";
  };

  if (["homeWinPct", "drawPct", "awayWinPct"].includes(columnKey)) return byThreshold(20, 35, 55);
  if (["homeXg", "awayXg"].includes(columnKey)) return byThreshold(0.8, 1.2, 1.8);
  if (columnKey === "totalXg") return byThreshold(2.0, 2.6, 3.2);
  if (["over25Pct", "bttsPct"].includes(columnKey)) return byThreshold(35, 50, 65);
  if (["homeCsPct", "awayCsPct"].includes(columnKey)) return byThreshold(20, 30, 45);
  if (["home2PlusPct", "away2PlusPct"].includes(columnKey)) return byThreshold(25, 40, 55);
  return "";
}

// points objective used for best XI
function objectivePoints(p, mode, startGw) {
  if (!p) return 0;
  if (mode === "gw" || mode === "single") {
    if (startGw != null) {
      const v = toNumOrNull(p?.[`pts_gw${startGw}`]);
      if (v != null) return v;
    }
    return n(p?.pts_next_sum);
  }
  const horizon = mode === "next5" ? 5 : 4;
  let sum = 0;
  let has = false;
  if (startGw != null) {
    for (let i = 0; i < horizon; i++) {
      const v = toNumOrNull(p?.[`pts_gw${startGw + i}`]);
      if (v != null) {
        sum += v;
        has = true;
      }
    }
  }
  return has ? sum : n(p?.pts_rest);
}

function sortByObjective(arr, mode, startGw) {
  return [...arr].sort((a, b) => {
    const diff = objectivePoints(b, mode, startGw) - objectivePoints(a, mode, startGw);
    if (diff !== 0) return diff;
    return String(a.id).localeCompare(String(b.id));
  });
}

function teamColors(team) {
  return TEAM_STYLES[team] || { a: "#4f4f4f", b: "#2f2f2f" };
}

// ---------- Shirt Icon (Option 2) ----------
function ShirtIcon({ primaryColor, secondaryColor }) {
  const striped = Boolean(secondaryColor && primaryColor !== secondaryColor);
  const pid = `shirt_${String(primaryColor).replace("#", "")}_${String(secondaryColor || "").replace("#", "")}`;
  return (
    <svg className="shirtIcon" viewBox="0 0 64 64" aria-hidden="true">
      {striped ? (
        <defs>
          <pattern id={pid} width="8" height="8" patternUnits="userSpaceOnUse">
            <rect width="4" height="8" fill={primaryColor} />
            <rect x="4" width="4" height="8" fill={secondaryColor} />
          </pattern>
        </defs>
      ) : null}
      <path
        d="M20 10l-8 6-6 2 5 11 7-3v28h28V26l7 3 5-11-6-2-8-6-8 6h-8z"
        fill={striped ? `url(#${pid})` : primaryColor}
        stroke="rgba(255,255,255,0.75)"
        strokeWidth="2"
      />
      <rect x="24" y="10" width="16" height="8" fill="rgba(255,255,255,0.18)" />
    </svg>
  );
}

// ---------- Best XI computation (formation enumeration) ----------
function computeBestXI(players, startGw, mode) {
  const pool = (players || [])
    .map(withNormPos)
    .filter((p) => ["GK", "DEF", "MID", "FWD"].includes(p.position_norm));

  const by = {
    GK: sortByObjective(pool.filter((p) => p.position_norm === "GK"), mode, startGw),
    DEF: sortByObjective(pool.filter((p) => p.position_norm === "DEF"), mode, startGw),
    MID: sortByObjective(pool.filter((p) => p.position_norm === "MID"), mode, startGw),
    FWD: sortByObjective(pool.filter((p) => p.position_norm === "FWD"), mode, startGw),
  };

  if (!by.GK.length) return { error: "No GK available." };
  if (by.DEF.length < MIN_XI.DEF) return { error: "Not enough DEF available." };
  if (by.MID.length < MIN_XI.MID) return { error: "Not enough MID available." };
  if (by.FWD.length < MIN_XI.FWD) return { error: "No FWD available." };

  let best = null;
  const pickTopWithTeamCap = (cands, need, seedCounts) => {
    const out = [];
    const tc = { ...seedCounts };
    for (const p of cands) {
      const tm = String(p.team || "");
      if ((tc[tm] || 0) >= TEAM_CAP) continue;
      out.push(p);
      tc[tm] = (tc[tm] || 0) + 1;
      if (out.length === need) return out;
    }
    return null;
  };

  for (let def = 3; def <= 5; def++) {
    for (let mid = 3; mid <= 5; mid++) {
      for (let fwd = 1; fwd <= 3; fwd++) {
        if (def + mid + fwd !== 10) continue;
        if (by.DEF.length < def || by.MID.length < mid || by.FWD.length < fwd) continue;

        const gk = by.GK[0];
        const seed = { [String(gk.team || "")]: 1 };
        const defs = pickTopWithTeamCap(by.DEF, def, seed);
        if (!defs) continue;
        const afterDef = { ...seed };
        for (const p of defs) afterDef[String(p.team || "")] = (afterDef[String(p.team || "")] || 0) + 1;
        const mids = pickTopWithTeamCap(by.MID, mid, afterDef);
        if (!mids) continue;
        const afterMid = { ...afterDef };
        for (const p of mids) afterMid[String(p.team || "")] = (afterMid[String(p.team || "")] || 0) + 1;
        const fwds = pickTopWithTeamCap(by.FWD, fwd, afterMid);
        if (!fwds) continue;

        const xi = [gk, ...defs, ...mids, ...fwds];
        const totalScore = xi.reduce((s, p) => s + objectivePoints(p, mode, startGw), 0);
        const formation = `${def}-${mid}-${fwd}`;

        if (!best || totalScore > best.totalScore || (totalScore === best.totalScore && formation < best.formation)) {
          best = {
            xi,
            rowsByPos: {
              GK: [by.GK[0]],
              DEF: by.DEF.slice(0, def),
              MID: by.MID.slice(0, mid),
              FWD: by.FWD.slice(0, fwd),
            },
            formation,
            totalScore,
          };
        }
      }
    }
  }

  if (!best) return { error: "Could not build a valid XI with available players." };
  return best;
}

function buildFromFixedSquad(squadPlayers, mode, startGw) {
  const sp = (squadPlayers || []).map(withNormPos);
  const byPos = {
    GK: sortByObjective(sp.filter((p) => p.position_norm === "GK"), mode, startGw),
    DEF: sortByObjective(sp.filter((p) => p.position_norm === "DEF"), mode, startGw),
    MID: sortByObjective(sp.filter((p) => p.position_norm === "MID"), mode, startGw),
    FWD: sortByObjective(sp.filter((p) => p.position_norm === "FWD"), mode, startGw),
  };

  if (byPos.GK.length < 2) return { error: "Manual squad must include 2 GKs." };
  if (byPos.DEF.length < MIN_XI.DEF || byPos.MID.length < MIN_XI.MID || byPos.FWD.length < MIN_XI.FWD)
    return { error: "Manual squad cannot form a valid XI (need 3 DEF, 3 MID, 1 FWD minimum)." };

  let best = null;

  // Enumerate valid formations (DEF 3-5, MID 3-5, FWD 1-3) with DEF+MID+FWD=10 outfield
  for (let def = 3; def <= 5; def++) {
    for (let mid = 3; mid <= 5; mid++) {
      for (let fwd = 1; fwd <= 3; fwd++) {
        if (def + mid + fwd !== 10) continue;
        if (byPos.DEF.length < def || byPos.MID.length < mid || byPos.FWD.length < fwd) continue;

        const xi = [byPos.GK[0], ...byPos.DEF.slice(0, def), ...byPos.MID.slice(0, mid), ...byPos.FWD.slice(0, fwd)];
        const score = xi.reduce((s, p) => s + objectivePoints(p, mode, startGw), 0);

        if (!best || score > best.score) best = { xi, score, formation: `${def}-${mid}-${fwd}` };
      }
    }
  }
  if (!best) return { error: "Could not build a valid XI from manual squad." };

  const xiIds = new Set(best.xi.map((p) => String(p.id)));
  const benchOut = sortByObjective(sp.filter((p) => p.position_norm !== "GK" && !xiIds.has(String(p.id))), mode, startGw).slice(0, 3);
  return { xi: best.xi, bench: [byPos.GK[1], ...benchOut], formation: best.formation };
}

function createEmptyManualSlots() {
  return {
    GK: Array(MANUAL_COUNTS.GK).fill(""),
    DEF: Array(MANUAL_COUNTS.DEF).fill(""),
    MID: Array(MANUAL_COUNTS.MID).fill(""),
    FWD: Array(MANUAL_COUNTS.FWD).fill(""),
  };
}

function PitchCard({ p, mode, startGw, captainId, viceCaptainId, onSetC, onSetV, showCaptainControls = true }) {
  const c = teamColors(p.team);
  const isC = String(captainId) === String(p.id);
  const isV = String(viceCaptainId) === String(p.id);

  return (
    <div className="pitchCard compact">
      {isC ? <span className="roleBadge roleC">C</span> : null}
      {isV ? <span className="roleBadge roleV">V</span> : null}

      <div className="shirtWrap">
        <ShirtIcon primaryColor={c.a} secondaryColor={c.b} />
      </div>

      <div className="pcName" title={p.player_name}>
        {p.player_name}
      </div>
      <div className="pcMeta">
        <span className="pill mini" style={{ background: teamBg(p.team), color: teamTextColor(p.team) }}>
          {p.team}
        </span>
        <span className="pill mini">{p.position_norm || normalizePosition(p)}</span>
      </div>
      <div className="pcPts">Proj: {intPts(objectivePoints(p, mode, startGw))}</div>

      {showCaptainControls ? (
        <div className="cvBtns">
          <button type="button" className={`miniBtn capBtn ${isC ? "active" : ""}`} onClick={() => onSetC(p.id)}>
            C
          </button>
          <button type="button" className={`miniBtn viceBtn ${isV ? "active" : ""}`} onClick={() => onSetV(p.id)}>
            V
          </button>
        </div>
      ) : null}
    </div>
  );
}

function PitchSlot({ slotId, p, mode, startGw, captainId, viceCaptainId, onSetC, onSetV, showCaptainControls = true }) {
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: slotId, data: { slotId } });
  const { attributes, listeners, setNodeRef: setDragRef, transform, isDragging } = useDraggable({
    id: `drag-${slotId}`,
    data: { slotId, playerId: p?.id, position: p?.position_norm },
  });
  const style = {
    transform: transform ? CSS.Translate.toString(transform) : undefined,
    opacity: isDragging ? 0.45 : 1,
  };
  return (
    <div ref={setDropRef} className={`dndSlot ${isOver ? "dndOver" : ""}`}>
      <div ref={setDragRef} style={style} {...attributes} {...listeners}>
        <PitchCard
          p={p}
          mode={mode}
          startGw={startGw}
          captainId={captainId}
          viceCaptainId={viceCaptainId}
          onSetC={onSetC}
          onSetV={onSetV}
          showCaptainControls={showCaptainControls}
        />
      </div>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState("table");
  const [players, setPlayers] = useState([]);
  const [nextGw, setNextGw] = useState(null);
  const [startGw, setStartGw] = useState(null);
  const [tableError, setTableError] = useState("");
  const [squadError, setSquadError] = useState("");
  const [manualError, setManualError] = useState("");
  const [teamRanks, setTeamRanks] = useState(null);
  const [toastMsg, setToastMsg] = useState("");

  const [sortKey, setSortKey] = useState("pts_next_sum");
  const [sortDir, setSortDir] = useState("desc");
  const [activeCol, setActiveCol] = useState(null);
  const [activeRowId, setActiveRowId] = useState(null);
  const [flashRowId, setFlashRowId] = useState(null);

  const [teamFilter, setTeamFilter] = useState("ALL");
  const [posFilter, setPosFilter] = useState("ALL");
  const [maxCost, setMaxCost] = useState(15.5);
  const [minProb, setMinProb] = useState(0);

  const [teamId, setTeamId] = useState("");
  const [squadIds, setSquadIds] = useState(new Set());
  const [loadedPicks, setLoadedPicks] = useState([]);
  const [bestGwOffset, setBestGwOffset] = useState(0);
  const [bestMode, setBestMode] = useState("single"); // single or overall

  const [builderMode, setBuilderMode] = useState("single"); // single or next5
  const [builderSource, setBuilderSource] = useState("auto"); // auto or manual
  const [builderStatus, setBuilderStatus] = useState("loading");
  const [builderError, setBuilderError] = useState("");
  const [builderReloadTick, setBuilderReloadTick] = useState(0);
  const [builderView, setBuilderView] = useState(null);

  const [captainId, setCaptainId] = useState(null);
  const [viceCaptainId, setViceCaptainId] = useState(null);

  const [manualSlots, setManualSlots] = useState(createEmptyManualSlots);
  const [manualConfirmedIds, setManualConfirmedIds] = useState([]);
  const [transferSettings, setTransferSettings] = useState({
    free_transfers: 1,
    bank: 0.0,
    hit_cost: 4,
    horizon: 1,
    apply_prob: true,
  });
  const [transferLoading, setTransferLoading] = useState(false);
  const [transferError, setTransferError] = useState("");
  const [transferResult, setTransferResult] = useState(null);
  const [showTransferRules, setShowTransferRules] = useState(false);
  const [selectedLeague, setSelectedLeague] = useState(null);
  const [leagueMode, setLeagueMode] = useState("predictions");
  const [leagueLoading, setLeagueLoading] = useState(false);
  const [leagueData, setLeagueData] = useState({ fixtures: [], predictions: [], table: [], error: "" });
  const [leagueTable, setLeagueTable] = useState({ rows: [], formMap: null, loading: false, error: "" });
  const [predMenuOpen, setPredMenuOpen] = useState(false);

  const rowRefs = useRef({});
  const flashTimeoutRef = useRef(null);

  const gwsShown = 3;

  const apiUrl = useMemo(() => {
    const sg = startGw ?? nextGw ?? "";
    return `/api/players?gws=${gwsShown}&include_with_prob=true${sg ? `&start_gw=${sg}` : ""}`;
  }, [startGw, nextGw]);

  // Fetch players
  useEffect(() => {
    setTableError("");
    fetch(apiUrl)
      .then(async (res) => {
        if (!res.ok) throw new Error(`API error ${res.status}`);
        return res.json();
      })
      .then((data) => {
        const raw = data.players || [];
        const normed = raw.map(withNormPos);
        setPlayers(normed);
        setNextGw(data.next_gw ?? null);
        setStartGw((p) => (p == null ? (data.start_gw ?? data.next_gw ?? null) : p));
      })
      .catch((e) => setTableError(String(e)));
  }, [apiUrl]);

  useEffect(() => {
    fetch("/api/epl_table")
      .then(async (r) => {
        if (!r.ok) throw new Error(`EPL table API error ${r.status}`);
        return r.json();
      })
      .then((data) => setTeamRanks(data?.ranks || null))
      .catch(() => setTeamRanks(null));
  }, []);

  // Restore manual squad
  useEffect(() => {
    try {
      const raw = localStorage.getItem(MANUAL_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (parsed?.slots) setManualSlots(parsed.slots);
      if (Array.isArray(parsed?.ids)) {
        setManualConfirmedIds(parsed.ids.map((x) => Number(x)).filter((x) => Number.isFinite(x)));
      }
    } catch {
      // ignore
    }
  }, []);

  const teams = useMemo(() => ["ALL", ...Array.from(new Set(players.map((p) => p.team).filter(Boolean))).sort()], [players]);

  const gwCols = useMemo(() => {
    const sg = startGw ?? nextGw;
    return sg ? [sg, sg + 1, sg + 2] : [];
  }, [startGw, nextGw]);

  const ranges = useMemo(() => {
    const mm = (k) => {
      const arr = players.map((p) => n(p[k], 0));
      return arr.length ? { min: Math.min(...arr), max: Math.max(...arr) } : { min: 0, max: 1 };
    };
    return {
      points_so_far: mm("points_so_far"),
      selected_pct: mm("selected_pct"),
      transfers_in_gw: mm("transfers_in_gw"),
      transfers_out_gw: mm("transfers_out_gw"),
      merit: mm("merit"),
      form: mm("form"),
      pts_rest: mm("pts_rest"),
    };
  }, [players]);

  // Table view rows
  const viewRows = useMemo(() => {
    const rows = players
      .filter((p) => (teamFilter === "ALL" ? true : p.team === teamFilter))
      .filter((p) => (posFilter === "ALL" ? true : p.position_norm === posFilter))
      .filter((p) => n(p.cost) <= n(maxCost))
      .filter((p) => n(p.prob_appear) >= n(minProb));

    const d = sortDir === "asc" ? 1 : -1;
    rows.sort((a, b) => {
      if (sortKey === "chance_play_pct") {
        const av = parsePercentFlexible(a?.chance_play_pct);
        const bv = parsePercentFlexible(b?.chance_play_pct);
        const aNull = av == null;
        const bNull = bv == null;
        if (aNull && bNull) return compareValues(a?.player_name, b?.player_name);
        if (aNull) return 1;
        if (bNull) return -1;
        const cmpPct = av === bv ? 0 : av < bv ? -1 : 1;
        return cmpPct * d;
      }

      // Fix: availability sorts should treat blanks as lowest (push them down in DESC and ASC when user wants 100% at top)
      // We already handle chance_play_pct above. For other numeric sorts, keep normal.
      const cmp = compareValues(a?.[sortKey], b?.[sortKey], sortKey);
      return cmp !== 0 ? cmp * d : compareValues(a?.player_name, b?.player_name);
    });
    return rows;
  }, [players, teamFilter, posFilter, maxCost, minProb, sortKey, sortDir]);

  const summary = useMemo(() => {
    if (!viewRows.length) return null;
    const topProjected = [...viewRows].sort((a, b) => objectivePoints(b, "gw", nextGw) - objectivePoints(a, "gw", nextGw))[0];
    const posWeight = (p) => {
      if (p.position_norm === "MID" || p.position_norm === "FWD") return 0.15;
      if (p.position_norm === "DEF") return 0.05;
      return 0;
    };
    const captainPool = viewRows.filter((p) => n(p.prob_appear, 0) >= 0.6);
    const captainBase = captainPool.length ? captainPool : viewRows;
    const bestCaptain = [...captainBase].sort(
      (a, b) => objectivePoints(b, "gw", nextGw) + posWeight(b) - (objectivePoints(a, "gw", nextGw) + posWeight(a))
    )[0];
    const mostIn = [...viewRows].sort((a, b) => n(b.transfers_in_gw) - n(a.transfers_in_gw))[0];
    const mostOut = [...viewRows].sort((a, b) => n(b.transfers_out_gw) - n(a.transfers_out_gw))[0];
    const formLeader = [...viewRows].sort((a, b) => n(b.form) - n(a.form))[0];
    return { topProjected, bestCaptain, mostIn, mostOut, formLeader };
  }, [viewRows, nextGw]);

  const manualConfirmedPlayers = useMemo(() => {
    const byId = new Map(players.map((p) => [Number(p.id), p]));
    return manualConfirmedIds.map((id) => byId.get(Number(id))).filter(Boolean);
  }, [manualConfirmedIds, players]);

  // Builder computation
  const builderResult = useMemo(() => {
    if (!players.length || nextGw == null) return { layout: null, error: "" };

    const scoreMode = builderMode === "single" ? "gw" : "next5";
    let xiResult = null;

    let bench = [];
    if (builderSource === "manual") {
      if (manualConfirmedPlayers.length !== 15) return { layout: null, error: "Manual squad not confirmed (need 15 players)." };
      const fixed = buildFromFixedSquad(manualConfirmedPlayers, scoreMode, nextGw);
      if (fixed.error) return { layout: null, error: fixed.error };
      bench = fixed.bench || [];
      xiResult = {
        xi: fixed.xi,
        rowsByPos: {
          GK: fixed.xi.filter((p) => p.position_norm === "GK"),
          DEF: fixed.xi.filter((p) => p.position_norm === "DEF"),
          MID: fixed.xi.filter((p) => p.position_norm === "MID"),
          FWD: fixed.xi.filter((p) => p.position_norm === "FWD"),
        },
        formation: fixed.formation,
        totalScore: fixed.xi.reduce((s, p) => s + objectivePoints(p, scoreMode, nextGw), 0),
      };
    } else {
      const xi = computeBestXI(players, nextGw, scoreMode);
      if (xi.error) return { layout: null, error: xi.error };
      xiResult = xi;
      const xiIds = new Set(xi.xi.map((p) => String(p.id)));
      const rem = sortByObjective(players.filter((p) => !xiIds.has(String(p.id))), scoreMode, nextGw);
      const gk = rem.find((p) => p.position_norm === "GK");
      const out = rem.filter((p) => p.position_norm !== "GK").slice(0, 3);
      bench = (gk ? [gk] : []).concat(out);
    }

    return {
      layout: {
        title: builderMode === "single" ? `Best Team for GW${nextGw}` : `Best Team for Next 5 GWs (from GW${nextGw})`,
        subtitle: builderSource === "manual" ? "Source: My Squad (Manual)" : "Source: Auto Best Team",
        rows: xiResult.rowsByPos,
        xi: xiResult.xi,
        formation: xiResult.formation,
        projected: xiResult.totalScore,
        bench,
      },
      error: "",
    };
  }, [players, nextGw, builderMode, builderSource, manualConfirmedPlayers, builderReloadTick]);

  const bestResult = useMemo(() => {
    if (!players.length || nextGw == null) return { layout: null, error: "" };
    const start = nextGw + (bestMode === "single" ? bestGwOffset : 0);
    const mode = bestMode === "single" ? "gw" : "range";
    const xi = computeBestXI(players, start, mode);
    if (xi.error) return { layout: null, error: xi.error };
    return {
      layout: {
        title: bestMode === "single" ? `Best Team for GW${start}` : `Overall Best Team (GW${nextGw}-GW${nextGw + 3})`,
        formation: xi.formation,
        projected: xi.totalScore,
        rows: xi.rowsByPos,
        mode,
        startGw: start,
      },
      error: "",
    };
  }, [players, nextGw, bestMode, bestGwOffset]);

  const builderLayout = builderResult.layout;

  useEffect(() => {
    if (!builderLayout) {
      setBuilderView(null);
      return;
    }
    setBuilderView({
      rows: {
        GK: [...(builderLayout.rows.GK || [])],
        DEF: [...(builderLayout.rows.DEF || [])],
        MID: [...(builderLayout.rows.MID || [])],
        FWD: [...(builderLayout.rows.FWD || [])],
      },
      bench: [...(builderLayout.bench || [])],
      formation: builderLayout.formation,
      projected: builderLayout.projected,
      title: builderLayout.title,
      subtitle: builderLayout.subtitle,
    });
  }, [builderLayout]);

  useEffect(() => {
    if (!players.length || nextGw == null) {
      setBuilderStatus("loading");
      setBuilderError("");
      return;
    }
    setBuilderStatus("loading");
    setBuilderError("");
    const t = setTimeout(() => {
      if (builderResult.layout) {
        setBuilderStatus("ready");
        setBuilderError("");
      } else {
        setBuilderStatus("error");
        setBuilderError(builderResult.error || "Could not compute best team.");
      }
    }, 0);
    return () => clearTimeout(t);
  }, [players, nextGw, builderResult]);

  // Default captain/vice (stable)
  const xiPlayers = useMemo(() => {
    if (!builderView) return [];
    return [...builderView.rows.GK, ...builderView.rows.DEF, ...builderView.rows.MID, ...builderView.rows.FWD];
  }, [builderView]);

  useEffect(() => {
    if (!xiPlayers.length) return;
    const first = xiPlayers[0];
    const second = xiPlayers.find((p) => String(p.id) !== String(first.id)) || first;

    setCaptainId((prev) => (xiPlayers.some((p) => String(p.id) === String(prev)) ? prev : first.id));
    setViceCaptainId((prev) => {
      const keep = xiPlayers.some((p) => String(p.id) === String(prev)) && String(prev) !== String(first.id);
      return keep ? prev : second.id;
    });
  }, [xiPlayers]);

  const captainPlayer = useMemo(() => xiPlayers.find((p) => String(p.id) === String(captainId)) || null, [xiPlayers, captainId]);
  const vicePlayer = useMemo(() => xiPlayers.find((p) => String(p.id) === String(viceCaptainId)) || null, [xiPlayers, viceCaptainId]);

  const xiNoCap = useMemo(() => {
    const scoreMode = builderMode === "single" ? "gw" : "next5";
    return xiPlayers.reduce((s, p) => s + objectivePoints(p, scoreMode, nextGw), 0);
  }, [xiPlayers, builderMode, nextGw]);
  const xiWithCap = useMemo(() => {
    if (!xiPlayers.length) return 0;
    const scoreMode = builderMode === "single" ? "gw" : "next5";
    return xiNoCap + objectivePoints(captainPlayer, scoreMode, nextGw);
  }, [xiPlayers, xiNoCap, captainPlayer, builderMode, nextGw]);

  const manualPreviewLayout = useMemo(() => {
    if (manualConfirmedPlayers.length !== 15 || nextGw == null) return null;
    const fixed = buildFromFixedSquad(manualConfirmedPlayers, "gw", nextGw);
    if (fixed.error) return null;
    return {
      rows: {
        GK: fixed.xi.filter((p) => p.position_norm === "GK"),
        DEF: fixed.xi.filter((p) => p.position_norm === "DEF"),
        MID: fixed.xi.filter((p) => p.position_norm === "MID"),
        FWD: fixed.xi.filter((p) => p.position_norm === "FWD"),
      },
      bench: fixed.bench,
      formation: fixed.formation,
    };
  }, [manualConfirmedPlayers, nextGw]);

  // sorting
  const onSort = (key) => {
    setActiveCol(key);
    // requested behavior: first click asc, second desc, toggles
    if (sortKey !== key) {
      setSortKey(key);
      setSortDir("asc");
      return;
    }
    setSortDir((d) => (d === "asc" ? "desc" : "asc"));
  };
  const arrow = (k) => (sortKey === k ? (sortDir === "asc" ? "▲" : "▼") : "");

  const focusPlayerRow = (pid) => {
    const row = rowRefs.current[String(pid)];
    if (!row) return;
    row.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    setActiveRowId(pid);
    setFlashRowId(pid);
    if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
    flashTimeoutRef.current = setTimeout(() => setFlashRowId(null), 1500);
  };

  const parseTeamId = (raw) => {
    const s = String(raw ?? "").trim();
    if (!/^\d+$/.test(s)) return null;
    const id = Number(s);
    return Number.isSafeInteger(id) && id > 0 ? id : null;
  };

  const loadSquad = async () => {
    setSquadError("");
    const id = parseTeamId(teamId);
    if (id == null) return setSquadError("Enter a valid Team ID (number).");
    try {
      const res = await fetch(`/api/squad?team_id=${id}`);
      if (!res.ok) throw new Error(`Squad API error ${res.status}`);
      const data = await res.json();
      const picks = data.picks || [];
      setLoadedPicks(picks);
      setSquadIds(new Set(picks.map((x) => x.element)));
      setPage("table");
    } catch (e) {
      setSquadError(String(e));
    }
  };

  const setCaptain = (id) => {
    if (!xiPlayers.some((p) => String(p.id) === String(id))) return;
    if (String(viceCaptainId) === String(id)) {
      const alt = xiPlayers.find((p) => String(p.id) !== String(id));
      if (alt) setViceCaptainId(alt.id);
    }
    setCaptainId(id);
  };

  const setVice = (id) => {
    if (!xiPlayers.some((p) => String(p.id) === String(id))) return;
    if (String(captainId) === String(id)) return;
    setViceCaptainId(id);
  };

  // Manual selection helpers
  const manualUsedIds = useMemo(() => {
    const out = new Set();
    Object.values(manualSlots).forEach((arr) => arr.forEach((id) => id !== "" && out.add(String(id))));
    return out;
  }, [manualSlots]);

  const playersById = useMemo(() => new Map(players.map((p) => [String(p.id), p])), [players]);

  const manualTeamCounts = useMemo(() => {
    const counts = {};
    for (const pos of ["GK", "DEF", "MID", "FWD"]) {
      for (const id of manualSlots[pos]) {
        if (!id) continue;
        const p = playersById.get(String(id));
        if (!p) continue;
        const t = String(p.team || "");
        counts[t] = (counts[t] || 0) + 1;
      }
    }
    return counts;
  }, [manualSlots, playersById]);

  const wouldExceedClubCap = (pos, idx, candidateId) => {
    const cand = playersById.get(String(candidateId));
    if (!cand) return false;
    const currentId = String(manualSlots[pos][idx] || "");
    const currentP = currentId ? playersById.get(currentId) : null;
    const team = String(cand.team || "");
    let currentCount = manualTeamCounts[team] || 0;
    if (currentP && String(currentP.team || "") === team) currentCount -= 1;
    return currentCount >= TEAM_CAP;
  };

  const updateManualSlot = (pos, idx, id) => {
    if (id && wouldExceedClubCap(pos, idx, id)) {
      setManualError("Max 3 players per club is allowed.");
      return;
    }
    setManualError("");
    setManualSlots((prev) => {
      const next = { ...prev, [pos]: [...prev[pos]] };
      next[pos][idx] = id;
      return next;
    });
  };

  const manualOptionList = (pos, idx) => {
    const current = String(manualSlots[pos][idx] || "");
    return players
      .filter((p) => p.position_norm === pos)
      .filter((p) => {
        const sid = String(p.id);
        return sid === current || !manualUsedIds.has(sid);
      })
      .sort((a, b) => a.player_name.localeCompare(b.player_name));
  };

  const manualComplete = useMemo(() => {
    const ids = [];
    for (const pos of ["GK", "DEF", "MID", "FWD"]) {
      for (const v of manualSlots[pos]) {
        if (!v) return false;
        ids.push(String(v));
      }
    }
    if (new Set(ids).size !== 15) return false;
    const counts = {};
    for (const id of ids) {
      const p = playersById.get(String(id));
      if (!p) continue;
      const t = String(p.team || "");
      counts[t] = (counts[t] || 0) + 1;
      if (counts[t] > TEAM_CAP) return false;
    }
    return true;
  }, [manualSlots, playersById]);

  const confirmManualSquad = () => {
    if (!manualComplete) {
      setSquadError("Manual squad must have 15 unique players and max 3 per club.");
      return;
    }
    const ids = []
      .concat(manualSlots.GK, manualSlots.DEF, manualSlots.MID, manualSlots.FWD)
      .map((x) => Number(x));
    setManualConfirmedIds(ids);
    setSquadIds(new Set(ids));
    setSquadError("");
    localStorage.setItem(MANUAL_STORAGE_KEY, JSON.stringify({ slots: manualSlots, ids }));
  };

  const clearManualSquad = () => {
    const empty = createEmptyManualSlots();
    setManualSlots(empty);
    setManualConfirmedIds([]);
    localStorage.removeItem(MANUAL_STORAGE_KEY);
  };

  const reloadBuilder = () => {
    setBuilderStatus("loading");
    setBuilderError("");
    setBuilderReloadTick((x) => x + 1);
  };

  const findSlotPlayer = (view, slotId) => {
    if (!view || !slotId) return null;
    const [area, pos, idxRaw] = String(slotId).split(":");
    const idx = Number(idxRaw);
    if (area === "XI") {
      const arr = view.rows[pos] || [];
      return arr[idx] || null;
    }
    if (area === "BENCH") {
      return view.bench[idx] || null;
    }
    return null;
  };

  const setSlotPlayer = (view, slotId, player) => {
    const [area, pos, idxRaw] = String(slotId).split(":");
    const idx = Number(idxRaw);
    if (area === "XI") {
      const arr = [...(view.rows[pos] || [])];
      arr[idx] = player;
      view.rows[pos] = arr;
      return;
    }
    if (area === "BENCH") {
      const arr = [...(view.bench || [])];
      arr[idx] = player;
      view.bench = arr;
    }
  };

  const handleDragEnd = (event) => {
    const activeSlot = event?.active?.data?.current?.slotId;
    const overSlot = event?.over?.id;
    if (!activeSlot || !overSlot || activeSlot === overSlot || !builderView) return;

    const aArea = String(activeSlot).split(":")[0];
    const bArea = String(overSlot).split(":")[0];
    if (!((aArea === "XI" && bArea === "BENCH") || (aArea === "BENCH" && bArea === "XI"))) return;

    const a = findSlotPlayer(builderView, activeSlot);
    const b = findSlotPlayer(builderView, overSlot);
    if (!a || !b) return;
    if (a.position_norm !== b.position_norm) {
      setToastMsg("Swap rejected: only like-for-like positions are allowed.");
      setTimeout(() => setToastMsg(""), 1400);
      return;
    }

    const next = {
      ...builderView,
      rows: {
        GK: [...builderView.rows.GK],
        DEF: [...builderView.rows.DEF],
        MID: [...builderView.rows.MID],
        FWD: [...builderView.rows.FWD],
      },
      bench: [...builderView.bench],
    };
    setSlotPlayer(next, activeSlot, b);
    setSlotPlayer(next, overSlot, a);
    setBuilderView(next);
  };

  const effectiveSquadIds = useMemo(() => {
    if (manualConfirmedIds.length === 15) return manualConfirmedIds;
    if (loadedPicks.length === 15) return loadedPicks.map((x) => Number(x.element)).filter((x) => Number.isFinite(x));
    return [];
  }, [manualConfirmedIds, loadedPicks]);

  const computeTransferSuggestions = async () => {
    if (effectiveSquadIds.length !== 15) {
      setTransferError("Load a 15-man squad first (Team ID or Confirm Manual Squad).");
      return;
    }
    setTransferLoading(true);
    setTransferError("");
    setTransferResult(null);
    try {
      const res = await fetch("/api/transfer_suggestions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          squad_ids: effectiveSquadIds,
          bank: Number(transferSettings.bank) || 0,
          free_transfers: Number(transferSettings.free_transfers) || 0,
          hit_cost: Number(transferSettings.hit_cost) || 4,
          horizon: Number(transferSettings.horizon) || 1,
          apply_prob: Boolean(transferSettings.apply_prob),
        }),
      });
      if (!res.ok) throw new Error(`Transfer API error ${res.status}`);
      const data = await res.json();
      if (data?.error) throw new Error(data.error);
      setTransferResult(data);
    } catch (e) {
      setTransferError(String(e));
    } finally {
      setTransferLoading(false);
    }
  };

  const loadLeagueFixtures = async (league) => {
    setLeagueLoading(true);
    setLeagueMode("fixtures");
    setSelectedLeague(league);
    setPage("league");
    setLeagueData({ fixtures: [], predictions: [], table: [], error: "" });
    setLeagueTable({ rows: [], formMap: null, loading: true, error: "" });
    try {
      const [fxRes, tblRes] = await Promise.all([
        fetch(`/api/league/${league.code}/fixtures?days=14`),
        fetch(`/api/league/${league.code}/table`),
      ]);
      if (!fxRes.ok) {
        const text = await fxRes.text().catch(() => "");
        throw new Error(`Fixtures endpoint unavailable (${fxRes.status}). ${text}`);
      }
      const fxData = await fxRes.json();
      setLeagueData({ fixtures: fxData?.fixtures || [], predictions: [], table: [], error: "" });

      if (!tblRes.ok) {
        const text = await tblRes.text().catch(() => "");
        setLeagueTable({ rows: [], formMap: null, loading: false, error: `Could not load league table (${tblRes.status}). ${text}` });
      } else {
        const tblData = await tblRes.json();
        const rows = Array.isArray(tblData?.table) ? tblData.table : [];
        const formMap = {};
        rows.forEach((r) => {
          const teamName = String(r?.team || r?.team_name || "").trim();
          const form = r?.form;
          if (teamName && typeof form === "string" && form.trim()) formMap[teamName] = form.trim();
        });
        setLeagueTable({ rows, formMap: Object.keys(formMap).length ? formMap : null, loading: false, error: "" });
      }
    } catch (e) {
      setLeagueData({ fixtures: [], predictions: [], table: [], error: `Could not load fixtures for ${league.name}. ${String(e)}` });
      setLeagueTable({ rows: [], formMap: null, loading: false, error: "" });
    } finally {
      setLeagueLoading(false);
    }
  };

  const loadLeaguePredictions = async (league) => {
    setLeagueLoading(true);
    setLeagueMode("predictions");
    setSelectedLeague(league);
    setPage("league");
    setLeagueData({ fixtures: [], predictions: [], table: [], error: "" });
    setLeagueTable({ rows: [], formMap: null, loading: true, error: "" });
    try {
      const [predRes, tblRes] = await Promise.all([
        fetch(`/api/league/${league.code}/predictions?days=14`),
        fetch(`/api/league/${league.code}/table`),
      ]);
      if (!predRes.ok) throw new Error(`Predictions endpoint unavailable (${predRes.status}).`);
      const predData = await predRes.json();
      setLeagueData({ fixtures: [], predictions: predData?.predictions || [], table: [], error: "" });

      if (!tblRes.ok) {
        const text = await tblRes.text().catch(() => "");
        setLeagueTable({ rows: [], formMap: null, loading: false, error: `Could not load league table (${tblRes.status}). ${text}` });
      } else {
        const tblData = await tblRes.json();
        const rows = Array.isArray(tblData?.table) ? tblData.table : [];
        const formMap = {};
        rows.forEach((r) => {
          const teamName = String(r?.team || r?.team_name || "").trim();
          const form = r?.form;
          if (teamName && typeof form === "string" && form.trim()) formMap[teamName] = form.trim();
        });
        setLeagueTable({ rows, formMap: Object.keys(formMap).length ? formMap : null, loading: false, error: "" });
      }
    } catch (e) {
      setLeagueData({ fixtures: [], predictions: [], table: [], error: `Could not load predictions for ${league.name}. ${String(e)}` });
      setLeagueTable({ rows: [], formMap: null, loading: false, error: "" });
    } finally {
      setLeagueLoading(false);
    }
  };

  const loadLeagueTable = async (league) => {
    setLeagueLoading(true);
    setLeagueMode("table");
    setSelectedLeague(league);
    setPage("league");
    setLeagueData({ fixtures: [], predictions: [], table: [], error: "" });
    setLeagueTable({ rows: [], formMap: null, loading: true, error: "" });
    try {
      const res = await fetch(`/api/league/${league.code}/table`);
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`Table endpoint unavailable (${res.status}). ${text}`);
      }
      const data = await res.json();
      const rows = Array.isArray(data?.table) ? data.table : [];
      const formMap = {};
      rows.forEach((r) => {
        const teamName = String(r?.team || r?.team_name || "").trim();
        const form = r?.form;
        if (teamName && typeof form === "string" && form.trim()) formMap[teamName] = form.trim();
      });
      setLeagueData({ fixtures: [], predictions: [], table: rows, error: "" });
      setLeagueTable({ rows, formMap: Object.keys(formMap).length ? formMap : null, loading: false, error: "" });
    } catch (e) {
      setLeagueData({ fixtures: [], predictions: [], table: [], error: `Could not load table for ${league.name}. ${String(e)}` });
      setLeagueTable({ rows: [], formMap: null, loading: false, error: "" });
    } finally {
      setLeagueLoading(false);
    }
  };

  const th = (key, label, cls = "") => (
    <th className={`${cls} sortable ${activeCol === key ? "colActive" : ""}`} onClick={() => onSort(key)}>
      <span className="thLabel">{label}</span>
      <span className="sortArrow">{arrow(key)}</span>
    </th>
  );

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand" onClick={() => setPage("table")} style={{ cursor: "pointer" }}>
          {APP_TITLE}
        </div>

        <nav className="nav">
          <button type="button" className={page === "table" ? "active" : ""} onClick={() => setPage("table")}>
            Home / Table
          </button>
          <button type="button" className={page === "load" ? "active" : ""} onClick={() => setPage("load")}>
            Load Squad
          </button>
          <button type="button" className={page === "builder" ? "active" : ""} onClick={() => setPage("builder")}>
            Squad Builder
          </button>
          <button type="button" className={page === "best" ? "active" : ""} onClick={() => setPage("best")}>
            Best Team (GW)
          </button>
          <div className="predWrap">
            <button type="button" className={`predBtn ${predMenuOpen ? "active" : ""}`} onClick={() => setPredMenuOpen((v) => !v)}>
              Predictions
            </button>
            {predMenuOpen ? (
              <div className="predMenu">
                {LEAGUE_CARDS.map((lg) => (
                  <button
                    key={`pred_${lg.code}`}
                    type="button"
                    className="predItem"
                    onClick={() => {
                      setPredMenuOpen(false);
                      loadLeagueFixtures(lg);
                    }}
                  >
                    {lg.name}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        </nav>
      </header>

      <main className="content">
        {/* LEAGUE PAGE */}
        {page === "league" ? (
          <section className="panel">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2>{selectedLeague?.name || "League"} - Next Matches & Predictions</h2>
              <button type="button" className="btn ghost" onClick={() => setPage("table")}>
                Home
              </button>
            </div>

            <div className="row">
              <button
                type="button"
                className={`btn ${leagueMode === "predictions" ? "" : "ghost"}`}
                onClick={() => selectedLeague && loadLeaguePredictions(selectedLeague)}
              >
                Predictions
              </button>
              <button
                type="button"
                className={`btn ${leagueMode === "fixtures" ? "" : "ghost"}`}
                onClick={() => selectedLeague && loadLeagueFixtures(selectedLeague)}
              >
                Fixtures
              </button>
              <button
                type="button"
                className={`btn ${leagueMode === "table" ? "" : "ghost"}`}
                onClick={() => selectedLeague && loadLeagueTable(selectedLeague)}
              >
                Table
              </button>
            </div>

            {leagueLoading ? <div className="muted">Loading league data...</div> : null}
            {leagueData.error ? <div className="error">{leagueData.error}</div> : null}

            <div className="leagueMainOnly">
                {leagueMode === "predictions" && !leagueLoading && !leagueData.error ? (
                  <div className="tableWrap">
                    <div className="heatLegend">Heatmap: red low → green high</div>
                    <table className="tbl">
                      <thead>
                        <tr className="predGroupRow">
                          <th colSpan={5}>MATCH INFO</th>
                          <th colSpan={4}>RESULT PROBABILITY</th>
                          <th colSpan={3}>EXPECTED GOALS MODEL</th>
                          <th colSpan={2}>GOAL ENVIRONMENT</th>
                          <th colSpan={2}>DEFENSIVE OUTLOOK</th>
                          <th colSpan={2}>ATTACKING UPSIDE</th>
                        </tr>
                        <tr>
                          <th title="Match pairing in home vs away format.">Match</th>
                          <th title="Home side for this fixture.">Home</th>
                          <th title="Away side for this fixture.">Away</th>
                          <th title="Scheduled kickoff in your local timezone.">Kickoff</th>
                          <th title="Primary scoreline predicted by the model.">Model Score</th>
                          <th title="Model probability that the home team wins.">Home Win Chance</th>
                          <th title="Model probability of a draw.">Draw Chance</th>
                          <th title="Model probability that the away team wins.">Away Win Chance</th>
                          <th title="Entropy of (Home/Draw/Away) probabilities. Higher = more uncertain.">Outcome Uncertainty</th>
                          <th title="Expected goals for the home side.">Home xG</th>
                          <th title="Expected goals for the away side.">Away xG</th>
                          <th title="Combined expected goals in this match.">Expected Game Goals</th>
                          <th title="Probability of over 2.5 total goals.">Over 2.5 Goals</th>
                          <th title="Probability both teams score at least once.">Both Teams Score</th>
                          <th title="Probability home side keeps a clean sheet.">Home Clean Sheet</th>
                          <th title="Probability away side keeps a clean sheet.">Away Clean Sheet</th>
                          <th title="Probability home side scores at least 2 goals.">Home 2+ Goals</th>
                          <th title="Probability away side scores at least 2 goals.">Away 2+ Goals</th>
                        </tr>
                      </thead>
                      <tbody>
                        {leagueData.predictions.map((r, idx) => (
                          <tr key={`lp_${r.match_id || idx}`}>
                            {(() => {
                              const pred = r.prediction || {};
                              const home = r.home_team_name || r.home || "";
                              const away = r.away_team_name || r.away || "";
                              const xgh = pred.xgH ?? pred.xgh ?? pred.lambda_h ?? pred.lambda_home;
                              const xga = pred.xgA ?? pred.xga ?? pred.lambda_a ?? pred.lambda_away;
                              const xgT =
                                pred.exp_goals_total ??
                                (Number.isFinite(Number(xgh)) && Number.isFinite(Number(xga)) ? Number(xgh) + Number(xga) : null);
                              const xghNum = Number(xgh);
                              const xgaNum = Number(xga);
                              const xgTNum = Number(xgT);
                              const pHome = pred.p_home_win ?? pred.p_home;
                              const pDraw = pred.p_draw;
                              const pAway = pred.p_away_win ?? pred.p_away;
                              const pOver25 = pred.p_over_2_5;
                              const pBtts = pred.p_btts;
                              const pHomeCs = pred.p_home_cs;
                              const pAwayCs = pred.p_away_cs;
                              const pHome2 = pred.p_home_2plus;
                              const pAway2 = pred.p_away_2plus;
                              const entropy = pred.outcome_entropy;
                              const pct = (v) => (Number.isFinite(Number(v)) ? `${(Number(v) * 100).toFixed(1)}%` : "-");
                              return (
                                <>
                                  <td>{home && away ? `${home} vs ${away}` : "-"}</td>
                                  <td>{home || "-"}</td>
                                  <td>{away || "-"}</td>
                                  <td>{r.utc_date ? new Date(r.utc_date).toLocaleString() : "-"}</td>
                                  <td>{pred.predicted_score || pred.most_likely_score || "-"}</td>
                                  <td className={getHeatClass("homeWinPct", pHome)}>{pct(pHome)}</td>
                                  <td className={getHeatClass("drawPct", pDraw)}>{pct(pDraw)}</td>
                                  <td className={getHeatClass("awayWinPct", pAway)}>{pct(pAway)}</td>
                                  <td>{Number.isFinite(Number(entropy)) ? Number(entropy).toFixed(3) : "-"}</td>
                                  <td className={getHeatClass("homeXg", xghNum)}>{Number.isFinite(xghNum) ? xghNum.toFixed(2) : "-"}</td>
                                  <td className={getHeatClass("awayXg", xgaNum)}>{Number.isFinite(xgaNum) ? xgaNum.toFixed(2) : "-"}</td>
                                  <td className={getHeatClass("totalXg", xgTNum)}>{Number.isFinite(xgTNum) ? xgTNum.toFixed(2) : "-"}</td>
                                  <td className={getHeatClass("over25Pct", pOver25)}>{pct(pOver25)}</td>
                                  <td className={getHeatClass("bttsPct", pBtts)}>{pct(pBtts)}</td>
                                  <td className={getHeatClass("homeCsPct", pHomeCs)}>{pct(pHomeCs)}</td>
                                  <td className={getHeatClass("awayCsPct", pAwayCs)}>{pct(pAwayCs)}</td>
                                  <td className={getHeatClass("home2PlusPct", pHome2)}>{pct(pHome2)}</td>
                                  <td className={getHeatClass("away2PlusPct", pAway2)}>{pct(pAway2)}</td>
                                </>
                              );
                            })()}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}

                {leagueMode === "fixtures" && !leagueLoading && !leagueData.error ? (
                  <div className="tableWrap">
                    <table className="tbl">
                      <thead>
                        <tr>
                          <th>Fixture</th>
                          <th>Date</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {leagueData.fixtures.map((r, idx) => (
                          <tr key={`lf_${r.match_id || idx}`}>
                            {(() => {
                              const when = r.date || r.utcDate || r.kickoff || "-";
                              const status = r.status || r.state || "-";
                              const home = r.home || r.homeTeam || "";
                              const away = r.away || r.awayTeam || "";
                              return (
                                <>
                                  <td>{home} vs {away}</td>
                                  <td>{when !== "-" ? new Date(when).toLocaleDateString() : "-"}</td>
                                  <td>{status}</td>
                                </>
                              );
                            })()}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}

                {leagueMode === "table" && !leagueLoading && !leagueData.error ? (
                  <div className="tableWrap">
                    <table className="tbl league-table">
                      <thead>
                        <tr>
                          <th>Pos</th>
                          <th>Team</th>
                          <th>P</th>
                          <th>W</th>
                          <th>D</th>
                          <th>L</th>
                          <th>GF</th>
                          <th>GA</th>
                          <th>GD</th>
                          <th>Pts</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(() => {
                          const rows = [...(leagueTable.rows || [])]
                            .map((r) => ({
                              position: Number(r.position ?? r.pos ?? 0),
                              team: r.team ?? r.team_name ?? "-",
                              playedGames: Number(r.playedGames ?? r.played ?? 0),
                              won: Number(r.won ?? 0),
                              draw: Number(r.draw ?? 0),
                              lost: Number(r.lost ?? 0),
                              goalsFor: Number(r.goalsFor ?? r.gf ?? 0),
                              goalsAgainst: Number(r.goalsAgainst ?? r.ga ?? 0),
                              goalDifference: Number(r.goalDifference ?? r.gd ?? 0),
                              points: Number(r.points ?? r.pts ?? 0),
                            }))
                            .sort((a, b) => a.position - b.position);
                          const totalTeams = rows.length;
                          const bottomStart = totalTeams - 2;
                          return rows.map((r, idx) => {
                            let posCls = "";
                            if (r.position >= 1 && r.position <= 4) posCls = "pos-ucl";
                            else if (r.position === 5) posCls = "pos-uel";
                            else if (r.position >= bottomStart) posCls = "pos-rel";
                            return (
                              <tr key={`ltb_${idx}`}>
                                <td className={`pos-cell ${posCls}`}>{r.position || "-"}</td>
                                <td>{r.team}</td>
                                <td>{r.playedGames}</td>
                                <td>{r.won}</td>
                                <td>{r.draw}</td>
                                <td>{r.lost}</td>
                                <td>{r.goalsFor}</td>
                                <td>{r.goalsAgainst}</td>
                                <td>{r.goalDifference}</td>
                                <td>{r.points}</td>
                              </tr>
                            );
                          });
                        })()}
                      </tbody>
                    </table>

                    <div className="leagueLegend">
                      <div className="legendCol">
                        <div className="legendTitle">Qualification/Relegation</div>
                        <div className="legendItem"><span className="legendSwatch swatch-ucl" /> UEFA Champions League group stage</div>
                        <div className="legendItem"><span className="legendSwatch swatch-uel" /> Europa League group stage</div>
                        <div className="legendItem"><span className="legendSwatch swatch-rel" /> Relegation</div>
                      </div>
                      <div className="legendCol">
                        <div className="legendTitle">Last 5 matches</div>
                        {leagueTable.formMap ? (
                          <div className="legendItemGroup">
                            <span className="formPill formW">W</span>
                            <span className="formPill formD">D</span>
                            <span className="formPill formL">L</span>
                          </div>
                        ) : (
                          <div className="muted">(Form unavailable)</div>
                        )}
                      </div>
                    </div>
                  </div>
                ) : null}
            </div>
          </section>
        ) : null}

        {/* LOAD SQUAD */}
        {page === "load" ? (
          <section className="panel">
            <h2>Load my FPL Squad by Team ID</h2>
            <p className="muted">
              Find Team ID in your FPL URL: <b>fantasy.premierleague.com/entry/123456/event/...</b>
            </p>

            <div className="row">
              <input className="input" placeholder="Team ID..." value={teamId} onChange={(e) => setTeamId(e.target.value)} />
              <button type="button" className="btn" onClick={loadSquad}>
                Load Last-Deadline Squad
              </button>
              <button
                type="button"
                className="btn ghost"
                onClick={() => {
                  setSquadIds(new Set());
                  setLoadedPicks([]);
                }}
              >
                Unload
              </button>
            </div>

            {squadError ? <p className="error">{squadError}</p> : null}
            {manualError ? <p className="error">{manualError}</p> : null}

            <h3 style={{ marginTop: 16 }}>Build My Squad Manually</h3>

            {/* VERTICAL sections (no overlap) */}
            <div className="manualStack">
              {["GK", "DEF", "MID", "FWD"].map((pos) => (
                <div key={pos} className="manualSection">
                  <div className="manualHeader">
                    <div className="muted">
                      <b>{pos}</b> x {MANUAL_COUNTS[pos]}
                    </div>
                  </div>

                  <div className="manualList">
                    {manualSlots[pos].map((slotId, idx) => (
                      <div key={`${pos}_${idx}`} className="manualRow">
                        <select value={slotId} onChange={(e) => updateManualSlot(pos, idx, e.target.value)}>
                          <option value="">Select {pos}</option>
                          {manualOptionList(pos, idx).map((p) => (
                            <option key={p.id} value={p.id} disabled={wouldExceedClubCap(pos, idx, p.id)}>
                              {p.player_name} | {p.team} | {p.position_norm} | £{n(p.cost).toFixed(1)} | GW{nextGw ?? "?"}:
                              {toNumOrNull(p?.[`pts_gw${nextGw}`]) != null
                                ? intPts(toNumOrNull(p?.[`pts_gw${nextGw}`]))
                                : intPts(n(p.pts_next_sum))}
                              {wouldExceedClubCap(pos, idx, p.id) ? " | Max 3 reached" : ""}
                            </option>
                          ))}
                        </select>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            <div className="row" style={{ marginTop: 10 }}>
              <button type="button" className="btn" onClick={confirmManualSquad}>
                Confirm My Squad
              </button>
              <button type="button" className="btn ghost" onClick={clearManualSquad}>
                Clear Manual Squad
              </button>
            </div>

            {/* Manual preview on pitch */}
            {manualPreviewLayout ? (
              <div className="builderWrap" style={{ marginTop: 12 }}>
                <div className="pitchHead">
                  <div className="muted">My Squad (Manual) • Preview XI</div>
                  <div className="formationTag">Formation: {manualPreviewLayout.formation}</div>
                </div>

                <div className="pitch pitchCompact">
                  <div className="pitchRow">{manualPreviewLayout.rows.FWD.map((p) => <PitchCard key={`ml_fwd_${p.id}`} p={p} mode="gw" startGw={nextGw} captainId={null} viceCaptainId={null} onSetC={() => {}} onSetV={() => {}} showCaptainControls={false} />)}</div>
                  <div className="pitchRow">{manualPreviewLayout.rows.MID.map((p) => <PitchCard key={`ml_mid_${p.id}`} p={p} mode="gw" startGw={nextGw} captainId={null} viceCaptainId={null} onSetC={() => {}} onSetV={() => {}} showCaptainControls={false} />)}</div>
                  <div className="pitchRow">{manualPreviewLayout.rows.DEF.map((p) => <PitchCard key={`ml_def_${p.id}`} p={p} mode="gw" startGw={nextGw} captainId={null} viceCaptainId={null} onSetC={() => {}} onSetV={() => {}} showCaptainControls={false} />)}</div>
                  <div className="pitchRow">{manualPreviewLayout.rows.GK.map((p) => <PitchCard key={`ml_gk_${p.id}`} p={p} mode="gw" startGw={nextGw} captainId={null} viceCaptainId={null} onSetC={() => {}} onSetV={() => {}} showCaptainControls={false} />)}</div>
                </div>

                <div className="benchRow">
                  <div className="muted">Bench</div>
                  <div className="benchGrid">
                    {manualPreviewLayout.bench.map((p) => (
                      <div key={`ml_bench_${p.id}`} className="benchCard compact">
                        <div className="benchShirt">
                          <ShirtIcon primaryColor={teamColors(p.team).a} secondaryColor={teamColors(p.team).b} />
                        </div>
                        <div className="benchName" title={p.player_name}>
                          {p.player_name}
                        </div>
                        <div className="benchMeta">
                          {p.team} • {p.position_norm}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : null}
          </section>
        ) : null}

        {/* SQUAD BUILDER */}
        {page === "builder" ? (
          <section className="panel">
            <h2>Squad Builder</h2>

            <div className="row">
              <label>
                Source
                <select value={builderSource} onChange={(e) => setBuilderSource(e.target.value)}>
                  <option value="auto">Best Team (Auto)</option>
                  <option value="manual">My Squad (Manual)</option>
                </select>
              </label>

              <label>
                Mode
                <select value={builderMode} onChange={(e) => setBuilderMode(e.target.value)}>
                  <option value="single">Best Team for GW{nextGw ?? "?"}</option>
                  <option value="next5">Best Team for Next 5 GWs</option>
                </select>
              </label>

              <button type="button" className="btn" onClick={reloadBuilder}>
                Reload Best Team
              </button>
            </div>

            {builderStatus === "loading" ? <div className="muted">Computing best team...</div> : null}
            {builderStatus === "error" ? <div className="error">{builderError || "Could not compute best team."}</div> : null}

            {builderStatus === "ready" && builderView ? (
              <div className="builderWrap">
                <div className="pitchHead">
                  <div className="muted">
                    {builderView.title} • <span className="muted">{builderView.subtitle}</span>
                  </div>
                  <div className="formationTag">Formation: {builderView.formation}</div>
                </div>

                <div className="builderMetrics">
                  <div>
                    Projected XI points (no captain boost): <b>{intPts(xiNoCap)}</b>
                  </div>
                  <div>
                    Projected XI points (with captain double): <b>{intPts(xiWithCap)}</b>
                  </div>
                </div>

                {/* Pitch View */}
                <DndContext onDragEnd={handleDragEnd}>
                  <div className="pitch pitchCompact">
                    <div className="pitchRow">
                      {builderView.rows.FWD.map((p, idx) => (
                        <PitchSlot
                          key={`fwd_${p.id}_${idx}`}
                          slotId={`XI:FWD:${idx}`}
                          p={p}
                          mode={builderMode === "single" ? "gw" : "next5"}
                          startGw={nextGw}
                          captainId={captainId}
                          viceCaptainId={viceCaptainId}
                          onSetC={setCaptain}
                          onSetV={setVice}
                        />
                      ))}
                    </div>

                    <div className="pitchRow">
                      {builderView.rows.MID.map((p, idx) => (
                        <PitchSlot
                          key={`mid_${p.id}_${idx}`}
                          slotId={`XI:MID:${idx}`}
                          p={p}
                          mode={builderMode === "single" ? "gw" : "next5"}
                          startGw={nextGw}
                          captainId={captainId}
                          viceCaptainId={viceCaptainId}
                          onSetC={setCaptain}
                          onSetV={setVice}
                        />
                      ))}
                    </div>

                    <div className="pitchRow">
                      {builderView.rows.DEF.map((p, idx) => (
                        <PitchSlot
                          key={`def_${p.id}_${idx}`}
                          slotId={`XI:DEF:${idx}`}
                          p={p}
                          mode={builderMode === "single" ? "gw" : "next5"}
                          startGw={nextGw}
                          captainId={captainId}
                          viceCaptainId={viceCaptainId}
                          onSetC={setCaptain}
                          onSetV={setVice}
                        />
                      ))}
                    </div>

                    <div className="pitchRow">
                      {builderView.rows.GK.map((p, idx) => (
                        <PitchSlot
                          key={`gk_${p.id}_${idx}`}
                          slotId={`XI:GK:${idx}`}
                          p={p}
                          mode={builderMode === "single" ? "gw" : "next5"}
                          startGw={nextGw}
                          captainId={captainId}
                          viceCaptainId={viceCaptainId}
                          onSetC={setCaptain}
                          onSetV={setVice}
                        />
                      ))}
                    </div>
                  </div>

                  <div className="benchRow">
                    <div className="muted">Bench (drag to swap with XI, like-for-like only)</div>
                    <div className="benchGrid">
                      {builderView.bench.map((p, idx) => (
                        <PitchSlot
                          key={`bench_${p.id}_${idx}`}
                          slotId={`BENCH:${p.position_norm}:${idx}`}
                          p={p}
                          mode={builderMode === "single" ? "gw" : "next5"}
                          startGw={nextGw}
                          captainId={captainId}
                          viceCaptainId={viceCaptainId}
                          onSetC={setCaptain}
                          onSetV={setVice}
                          showCaptainControls={false}
                        />
                      ))}
                    </div>
                  </div>
                </DndContext>

                {toastMsg ? <div className="toastMsg">{toastMsg}</div> : null}

                <div className="captainNote">
                  Captain points are doubled for the Gameweek. If Captain plays 0 minutes, Vice-Captain gets doubled points instead. If neither plays, no double points are awarded.
                </div>
                <div className="muted">
                  Captain: <b>{captainPlayer?.player_name || "-"}</b> | Vice-Captain: <b>{vicePlayer?.player_name || "-"}</b>
                </div>

                <div className="transferPanel">
                  <h3>Transfer Suggestions</h3>
                  <div className="transferGrid">
                    <label>
                      Free Transfers
                      <select
                        value={transferSettings.free_transfers}
                        onChange={(e) => setTransferSettings((s) => ({ ...s, free_transfers: Number(e.target.value) }))}
                      >
                        {[0, 1, 2, 3, 4, 5].map((x) => (
                          <option key={x} value={x}>
                            {x}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Bank (£)
                      <input type="number" step="0.1" value={transferSettings.bank} onChange={(e) => setTransferSettings((s) => ({ ...s, bank: e.target.value }))} />
                    </label>
                    <label>
                      Hit Cost
                      <input type="number" step="1" value={transferSettings.hit_cost} onChange={(e) => setTransferSettings((s) => ({ ...s, hit_cost: e.target.value }))} />
                    </label>
                    <label>
                      Horizon
                      <select value={transferSettings.horizon} onChange={(e) => setTransferSettings((s) => ({ ...s, horizon: Number(e.target.value) }))}>
                        <option value={1}>GW only</option>
                        <option value={5}>Next 5 GWs</option>
                      </select>
                    </label>
                    <label className="checkRow">
                      <input
                        type="checkbox"
                        checked={transferSettings.apply_prob}
                        onChange={(e) => setTransferSettings((s) => ({ ...s, apply_prob: e.target.checked }))}
                      />
                      Apply appearance probability
                    </label>
                  </div>
                  <div className="row">
                    <button type="button" className="btn" onClick={computeTransferSuggestions} disabled={transferLoading}>
                      {transferLoading ? "Computing..." : "Compute Transfer Suggestions"}
                    </button>
                  </div>
                  {transferError ? <div className="error">{transferError}</div> : null}
                  {transferResult?.baseline ? (
                    <div className="muted" style={{ marginTop: 8 }}>
                      Baseline: {transferResult.baseline.formation} • Projected {intPts(transferResult.baseline.projected)}
                    </div>
                  ) : null}
                  {transferResult?.suggestions?.length ? (
                    <div className="transferTableWrap">
                      <table className="tbl transferTbl">
                        <thead>
                          <tr>
                            <th>Type</th>
                            <th>Out</th>
                            <th>In</th>
                            <th>Projected</th>
                            <th>Gain</th>
                          </tr>
                        </thead>
                        <tbody>
                          {transferResult.suggestions.map((s, idx) => (
                            <tr key={`s_${idx}`}>
                              <td>{s.type}</td>
                              <td>{(s.transfers_out || []).map((x) => `${x.name} (${x.team})`).join(", ") || "-"}</td>
                              <td>{(s.transfers_in || []).map((x) => `${x.name} (${x.team})`).join(", ") || "-"}</td>
                              <td>{intPts(s.projected)}</td>
                              <td>{Number(s.gain).toFixed(1)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : null}

                  <button type="button" className="btn ghost" onClick={() => setShowTransferRules((x) => !x)} style={{ marginTop: 10 }}>
                    {showTransferRules ? "Hide Rules" : "Show Rules"}
                  </button>
                  {showTransferRules ? (
                    <div className="captainNote">
                      FT: you gain 1 free transfer per GW (can carry up to 5). Extra transfers cost -4 by default.
                      Captain doubles points; Vice-Captain gets the double if captain plays 0 minutes.
                      Chips (Bench Boost, Triple Captain, Wildcard, Free Hit) are single-use and should be tracked outside this panel.
                    </div>
                  ) : null}
                </div>
              </div>
            ) : null}
          </section>
        ) : null}

        {/* BEST TEAM */}
        {page === "best" ? (
          <section className="panel">
            <h2>Best Team Suggestion</h2>
            {!players.length || nextGw == null ? <div className="muted">Loading players...</div> : null}

            {players.length && nextGw != null ? (
              <div className="builderWrap">
                <div className="row">
                  <label>
                    Mode
                    <select
                      value={bestMode === "overall" ? "overall" : String(bestGwOffset)}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === "overall") {
                          setBestMode("overall");
                          return;
                        }
                        setBestMode("single");
                        setBestGwOffset(n(v, 0));
                      }}
                    >
                      <option value="0">Best Team for GW{nextGw}</option>
                      <option value="1">Best Team for GW{nextGw + 1}</option>
                      <option value="2">Best Team for GW{nextGw + 2}</option>
                      <option value="3">Best Team for GW{nextGw + 3}</option>
                      <option value="overall">Overall Best Team (GW{nextGw}-GW{nextGw + 3})</option>
                    </select>
                  </label>
                </div>

                {bestResult.error ? <div className="error">{bestResult.error}</div> : null}
                {bestResult.layout ? (
                  <>
                    <div className="pitchHead">
                      <div className="muted">{bestResult.layout.title}</div>
                      <div className="formationTag">Formation: {bestResult.layout.formation}</div>
                    </div>
                    <div className="builderMetrics">
                      <div>
                        Projected XI points: <b>{intPts(bestResult.layout.projected)}</b>
                      </div>
                    </div>
                    <div className="pitch pitchCompact">
                      <div className="pitchRow">{bestResult.layout.rows.FWD.map((p) => <PitchCard key={`best_fwd_${p.id}`} p={p} mode={bestResult.layout.mode} startGw={bestResult.layout.startGw} showCaptainControls={false} />)}</div>
                      <div className="pitchRow">{bestResult.layout.rows.MID.map((p) => <PitchCard key={`best_mid_${p.id}`} p={p} mode={bestResult.layout.mode} startGw={bestResult.layout.startGw} showCaptainControls={false} />)}</div>
                      <div className="pitchRow">{bestResult.layout.rows.DEF.map((p) => <PitchCard key={`best_def_${p.id}`} p={p} mode={bestResult.layout.mode} startGw={bestResult.layout.startGw} showCaptainControls={false} />)}</div>
                      <div className="pitchRow">{bestResult.layout.rows.GK.map((p) => <PitchCard key={`best_gk_${p.id}`} p={p} mode={bestResult.layout.mode} startGw={bestResult.layout.startGw} showCaptainControls={false} />)}</div>
                    </div>
                  </>
                ) : null}
              </div>
            ) : null}
          </section>
        ) : null}

        {/* TABLE */}
        {page === "table" ? (
          <>
            <section className="filters">
              <div className="filterRow">
                <label>
                  Team
                  <select value={teamFilter} onChange={(e) => setTeamFilter(e.target.value)}>
                    {teams.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  Position
                  <select value={posFilter} onChange={(e) => setPosFilter(e.target.value)}>
                    {POS_FILTER.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  Max Cost
                  <input type="number" value={maxCost} step="0.1" onChange={(e) => setMaxCost(e.target.value)} />
                </label>

                <label>
                  Min Prob Appear
                  <input type="number" value={minProb} step="0.05" onChange={(e) => setMinProb(e.target.value)} />
                </label>

                <label>
                  Start GW
                  <input type="number" value={startGw ?? ""} onChange={(e) => setStartGw(Number(e.target.value))} placeholder={String(nextGw ?? "")} />
                </label>

                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => {
                    setTeamFilter("ALL");
                    setPosFilter("ALL");
                    setMaxCost(15.5);
                    setMinProb(0);
                    setStartGw(nextGw);
                  }}
                >
                  Clear Filters
                </button>
              </div>

              <div className="metaRow">
                <span className="muted">Rows: {viewRows.length}</span>
                {tableError ? <span className="error">{tableError}</span> : null}
              </div>
            </section>

            {summary ? (
              <section className="insightsBar">
                <div className="insightCard stat-card insightClickable" onClick={() => focusPlayerRow(summary.topProjected.id)}>
                  <div className="insightLabel">Top Projected (GW{nextGw ?? "?"})</div>
                  <div className="insightValue">{summary.topProjected.player_name}</div>
                  <div className="muted">Proj: {intPts(objectivePoints(summary.topProjected, "gw", nextGw))}</div>
                </div>

                <div className="insightCard stat-card insightClickable" onClick={() => focusPlayerRow(summary.bestCaptain.id)}>
                  <div className="insightLabel">Best Captain Candidate</div>
                  <div className="insightValue">{summary.bestCaptain.player_name}</div>
                  <div className="muted">
                    {summary.bestCaptain.position_norm} • Proj: {intPts(objectivePoints(summary.bestCaptain, "gw", nextGw))}
                  </div>
                </div>

                <div className="insightCard stat-card insightClickable" onClick={() => focusPlayerRow(summary.mostIn.id)}>
                  <div className="insightLabel">Most Transferred In</div>
                  <div className="insightValue">{summary.mostIn.player_name}</div>
                  <div className="muted">In GW: {n(summary.mostIn.transfers_in_gw)}</div>
                </div>

                <div className="insightCard stat-card transfer-out insightClickable" onClick={() => focusPlayerRow(summary.mostOut.id)}>
                  <div className="insightLabel">Most Transferred Out</div>
                  <div className="insightValue">{summary.mostOut.player_name}</div>
                  <div className="muted">Out GW: {n(summary.mostOut.transfers_out_gw)}</div>
                </div>

                <div className="insightCard stat-card insightClickable" onClick={() => focusPlayerRow(summary.formLeader.id)}>
                  <div className="insightLabel">Form Leader</div>
                  <div className="insightValue">{summary.formLeader.player_name}</div>
                  <div className="muted">Form: {n(summary.formLeader.form).toFixed(2)}</div>
                </div>
              </section>
            ) : null}

            <section className="tableWrap">
              <table className="tbl">
                <thead>
                  <tr>
                    {th("player_name", "Player", "stickyCol cPlayer")}
                    {th("team", "Team", "cTeam")}
                    {th("position_norm", "Pos")}
                    {th("cost", "Cost")}
                    {th("merit", "Merit")}
                    {th("form", "Form")}
                    {th("next_opponent", "Next Opp")}
                    {th("prob_appear", "Prob Appear")}
                    {th("chance_play_pct", "Availability %")}
                    {gwCols.map((gw) => th(`pts_gw${gw}`, `Pts GW${gw}`, ""))}
                    {th("pts_rest", "Pts Rest")}
                    {th("value_rest", "Val Rest")}
                    {th("points_so_far", "Points So Far")}
                    {th("selected_pct", "Selected %")}
                    {th("transfers_in_gw", "Transfers In GW")}
                    {th("transfers_out_gw", "Transfers Out GW")}
                  </tr>
                </thead>

                <tbody>
                  {viewRows.map((p) => {
                    const inSquad = squadIds.has(p.id);
                    const isSelected = activeRowId === p.id;
                    const isFlash = String(flashRowId) === String(p.id);

                    const meritBg = heatRedGreen(n(p.merit), ranges.merit.min, ranges.merit.max);
                    const formBg = heatRedGreen(n(p.form), ranges.form.min, ranges.form.max);
                    const ptsRestBg = heatRedGreen(n(p.pts_rest), ranges.pts_rest.min, ranges.pts_rest.max, true);

                    const nextFdr = nextGw ? n(p[`fdr_gw${nextGw}`], NaN) : NaN;
                    const chance = parsePercentFlexible(p.chance_play_pct);
                    const oppRankClass = oppDifficultyClass(p.team, p.next_opponent, teamRanks);

                    return (
                      <tr
                        id={`row-${p.id}`}
                        key={p.id}
                        ref={(el) => {
                          rowRefs.current[String(p.id)] = el;
                        }}
                        className={[isSelected ? "rowSelected" : "", inSquad ? "rowInSquad" : "", isFlash ? "rowFlash" : ""].join(" ")}
                        onClick={() => setActiveRowId(p.id)}
                      >
                        <td className={"stickyCol cPlayer " + (activeCol === "player_name" ? "colActiveCell" : "")}>{p.player_name}</td>

                        <td
                          className={"cTeam " + (activeCol === "team" ? "colActiveCell" : "")}
                          style={{ background: teamBg(p.team), color: teamTextColor(p.team), fontWeight: 800 }}
                        >
                          {p.team}
                        </td>

                        <td className={activeCol === "position_norm" ? "colActiveCell" : ""}>{p.position_norm}</td>

                        <td className={activeCol === "cost" ? "colActiveCell" : ""}>{n(p.cost).toFixed(1)}</td>

                        <td className={activeCol === "merit" ? "colActiveCell" : ""} style={{ background: meritBg }}>
                          {n(p.merit).toFixed(2)}
                        </td>

                        <td className={activeCol === "form" ? "colActiveCell" : ""} style={{ background: formBg }}>
                          {n(p.form).toFixed(2)}
                        </td>

                        <td className={[activeCol === "next_opponent" ? "colActiveCell" : "", oppRankClass || oppFdrCls(nextFdr)].join(" ")}>
                          {p.next_opponent ? (
                            <span className="opp">
                              {p.next_opponent}
                              {p.next_is_home === true ? " (H)" : p.next_is_home === false ? " (A)" : ""}
                            </span>
                          ) : (
                            "-"
                          )}
                        </td>

                        <td className={[probCls(n(p.prob_appear)), activeCol === "prob_appear" ? "colActiveCell" : ""].join(" ")}>
                          {n(p.prob_appear).toFixed(2)}
                        </td>

                        {/* Availability: blanks should NOT sort above 100%. Sorting already fixes blanks. */}
                        <td className={[availCls(chance), activeCol === "chance_play_pct" ? "colActiveCell" : ""].join(" ")}>
                          {chance == null ? "-" : `${Math.round(chance)}%`}
                        </td>

                        {gwCols.map((gw) => {
                          const key = `pts_gw${gw}`;
                          const g = Math.round(n(p[key]));
                          return (
                            <td key={key} className={[gwCls(g), activeCol === key ? "colActiveCell" : ""].join(" ")}>
                              {g}
                            </td>
                          );
                        })}

                        <td className={activeCol === "pts_rest" ? "colActiveCell" : ""} style={{ background: ptsRestBg }}>
                          {n(p.pts_rest).toFixed(1)}
                        </td>

                        <td className={activeCol === "value_rest" ? "colActiveCell" : ""}>{n(p.value_rest).toFixed(2)}</td>

                        <td
                          className={activeCol === "points_so_far" ? "colActiveCell" : ""}
                          style={{ background: heatLegacy(n(p.points_so_far), ranges.points_so_far.min, ranges.points_so_far.max) }}
                        >
                          {n(p.points_so_far)}
                        </td>

                        <td
                          className={activeCol === "selected_pct" ? "colActiveCell" : ""}
                          style={{ background: heatLegacy(n(p.selected_pct), ranges.selected_pct.min, ranges.selected_pct.max) }}
                        >
                          {n(p.selected_pct).toFixed(1)}
                        </td>

                        <td
                          className={activeCol === "transfers_in_gw" ? "colActiveCell" : ""}
                          style={{ background: heatLegacy(n(p.transfers_in_gw), ranges.transfers_in_gw.min, ranges.transfers_in_gw.max) }}
                        >
                          {n(p.transfers_in_gw)}
                        </td>

                        <td
                          className={activeCol === "transfers_out_gw" ? "colActiveCell" : ""}
                          style={{ background: heatLegacy(n(p.transfers_out_gw), ranges.transfers_out_gw.min, ranges.transfers_out_gw.max) }}
                        >
                          {n(p.transfers_out_gw)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          </>
        ) : null}
      </main>

      <footer className="footer">RUTEJ TALATI (c) 2026</footer>
    </div>
  );
}
