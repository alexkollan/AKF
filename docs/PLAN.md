# WoW Forever Fishing Bot — Development Plan

Nov 20, 2026 · @Alex

## Επισκόπηση

Χτίζεται ένα fishing pixel bot με δικό του UI στη δεύτερη οθόνη (8 milestones). Ο κοινός πυρήνας του (capture, input, engine service, UI shell) γίνεται μετά η βάση για το grind bot, που έχει δικό του doc: Doc.

**Τι παραδίδεται**

- Start/Stop/Pause, 3 τρόποι ψαρέματος (single-button addon, right-click στο bobber, interact key).
- Ρυθμίσεις για lures, hotkeys, profiles, live στατιστικά και calibration wizard.

**Πώς το δουλεύεις με το Claude Code**

1. Φτιάχνεις ένα άδειο repo και βάζεις αυτό το doc ως `docs/PLAN.md` (Export σε Markdown).
2. Στο milestone M0 το Claude Code γράφει το `CLAUDE.md` με τις αποφάσεις stack και conventions από εδώ.
3. Προχωράς ένα milestone τη φορά: κάνεις paste το prompt του milestone και ελέγχεις τα acceptance criteria πριν το επόμενο.
4. Ό,τι θέλει το παιχνίδι ανοιχτό (calibration, ηχογραφήσεις, έλεγχοι API) το κάνεις εσύ. Το Claude Code γράφει τα εργαλεία και τα tests με recorded δείγματα.

Τα prompts για το Claude Code είναι στα αγγλικά για να γίνονται κατευθείαν paste. Το υπόλοιπο doc είναι στα ελληνικά.

## WoW Forever API και fishing bot

Το fishing bot δεν διαβάζει τίποτα από το API του παιχνιδιού, οπότε οι περιορισμοί του Forever (secret values) δεν το επηρεάζουν. Η πλήρης έρευνα API είναι στο Doc.

- Client 1.60.1, TOC `## Interface: 16001` (για το προαιρετικό FishBridge addon).
- **Το τσίμπημα δεν φαίνεται στο API.** Το παιχνίδι το μπλοκάρει εσκεμμένα ([Campfire Angler](https://www.curseforge.com/wow/addons/campfire-angler)), οπότε η ανίχνευση γίνεται μόνο από εικόνα ή ήχο.
- Το soft-target interact υπάρχει (CVars `SoftTargetInteract`, `SoftTargetInteractRange`). Αυτό χρησιμοποιούν τα one-key fishing addons και το mode C ([EzFishing Forever](https://www.curseforge.com/wow/addons/ezfishing-forever)).
- Το FishBridge διαβάζει μόνο μη-secret API (bags, combat flag, equipped item), οπότε δεν επηρεάζεται από τους περιορισμούς.

## Fishing bot: stack και αρχιτεκτονική

Η επιλογή είναι Python engine με React UI σε desktop παράθυρο. Το Python έχει το πιο ώριμο stack για capture, OpenCV και κυρίως για audio loopback. Το UI γίνεται σε React/TypeScript, που το ξέρεις ήδη. Επικοινωνούν μέσω local WebSocket, οπότε το ίδιο UI shell ξαναχρησιμοποιείται αυτούσιο στο grind bot.

&#91;embedded content: αρχιτεκτονική fishing bot · engine + UI\]

Όλη η λογική (ανίχνευση, state machine, input) τρέχει στο engine. Το UI στέλνει μόνο εντολές και ρυθμίσεις μέσω WebSocket, οπότε το bot τρέχει και με κλειστό UI.

| Κομμάτι | Επιλογή | Γιατί |
| --- | --- | --- |
| Runtime | Python 3.12, Windows 10/11 | Η καλύτερη υποστήριξη για capture, vision και audio |
| Screen capture | `dxcam` (DXGI, πάνω από 100 fps), fallback `mss` | Γρήγορο capture της οθόνης του WoW |
| Vision | `opencv-python` + `numpy` | HSV threshold, template matching, frame diff |
| Audio | `PyAudioWPatch` (WASAPI loopback) | Ακούει το splash απευθείας από την έξοδο ήχου |
| Input | `SendInput` μέσω `ctypes` (ή `pydirectinput`), `pywin32` για focus παραθύρου | Scancodes που τα δέχεται το παιχνίδι |
| Global hotkeys | `pynput` | Start/Pause/Stop ακόμα κι όταν focus έχει το WoW |
| Engine service | `FastAPI` + WebSocket (`uvicorn`), `pydantic` για config | Commands από το UI, live events/στατιστικά προς το UI |
| UI | React + Vite + TypeScript, Tailwind + shadcn/ui, Zustand | Σύγχρονο, οικείο σε σένα |
| Desktop shell | `pywebview` (φορτώνει το UI από localhost) | Ένα παράθυρο στη 2η οθόνη, χωρίς Electron |
| Packaging | `PyInstaller` (one-folder) | Ένα `.exe` για να το τρέχεις |
| Tests | `pytest` με recorded clips (frames + wav) | Η ανίχνευση ελέγχεται χωρίς να τρέχει το παιχνίδι |

**Δομή repo**

```
AFK/
  CLAUDE.md
  docs/PLAN.md
  engine/
    capture.py        # dxcam/mss, WoW window + monitor discovery, DPI aware
    input.py          # SendInput scancodes, mouse move/click, window focus
    audio.py          # WASAPI loopback stream, splash detector
    vision/bobber.py  # find bobber: HSV mask + template + blob scoring
    vision/bite.py    # bite: drop/disappear/motion in ROI
    fsm.py            # fishing state machine, modes A/B/C
    lures.py          # lure timer + apply sequence
    config.py         # pydantic models, profiles (JSON)
    stats.py          # session stats, catch log
    server.py         # FastAPI + WebSocket API
  ui/                 # Vite + React + TS
  tools/
    record_clip.py    # records frames + audio of real casts
    replay.py         # runs detectors on recorded clips
  tests/fixtures/     # recorded clips, labelled
```

**Δύο τεχνικά θέματα που λύνονται από την αρχή**

- **Focus:** το `SendInput` στέλνει πλήκτρα στο παράθυρο που έχει focus. Στο Start το engine κάνει focus το WoW, και όσο τρέχει το UI δεν κλέβει focus. Αν δει ότι το WoW έχασε το focus, κάνει auto-pause. Το background input με `PostMessage` είναι πειραματικό option για δοκιμή.
- **Συντεταγμένες σε δύο οθόνες:** per-monitor DPI awareness (`SetProcessDpiAwarenessContext`) και όλες οι συντεταγμένες σχετικά με το client area του WoW. Έτσι το right-click πέφτει σωστά ακόμα κι αν οι οθόνες έχουν διαφορετικό scaling.

## Fishing bot: modes, ανίχνευση και loop

Το bot έχει ένα κοινό loop και τρεις τρόπους για το «μάζεμα». Η ανίχνευση τσιμπήματος γίνεται με ήχο, με εικόνα ή με το συνδυασμό τους. Default είναι ο ήχος, γιατί δεν επηρεάζεται από νερό, φωτισμό και camera.

**Τρόποι λειτουργίας**

| Mode | Cast | Hook + loot | Χρειάζεται εντοπισμό bobber; | Προαπαιτούμενα |
| --- | --- | --- | --- | --- |
| **A. Single-button (addon)** | `fishKey` | Ξανά `fishKey`. Το addon σου το κάνει interact + loot | Όχι (με audio) | Το one-key addon σου ενεργό |
| **B. Right-click bobber** | `castKey` | Το ποντίκι πάει στο bobber, right-click (Shift+right-click αν το auto-loot είναι off) | Ναι | Auto Loot on (ή επιλογή Shift) |
| **C. Interact key (χωρίς addon)** | `castKey` | `interactKey` (soft-target interact) | Όχι (με audio) | Enable Interact Key, `/console SoftTargetInteractRange 30`, κανένα target επιλεγμένο |

Στα modes A και C μπορεί να δουλέψει μόνο με ήχο, χωρίς vision. Το vision χρειάζεται μόνο για το mode B ή για pixel-only ανίχνευση.

**Εντοπισμός bobber (vision/bobber.py)**

1. Αναμονή μέχρι να πέσει το bobber (`landDelayMs`, ρυθμίζεται στο calibration).
2. Capture του search ROI (default: το κεντρικό 60% του client area, ρυθμιζόμενο στο UI).
3. HSV mask για το κόκκινο φτερό (και μπλε preset για νύχτα/σκοτεινά νερά), μετά morphology και connected components.
4. Score ανά blob (μέγεθος, σχήμα, απόσταση από το προηγούμενο cast). Fallback: template matching με τα bobber templates που καταγράφεις στο calibration.
5. Αποτέλεσμα: θέση, bounding box και confidence. Αν το confidence είναι κάτω από το threshold, καταγράφεται miss και γίνεται recast.

**Ανίχνευση τσιμπήματος**

- **Audio (audio.py):** WASAPI loopback stream. Υπολογίζει ενέργεια ανά μικρό παράθυρο και τη συγκρίνει με κινούμενο noise floor (rolling median). Προαιρετικά κάνει και cross-correlation με ένα ηχογραφημένο δείγμα splash. Θέλει ήχο παιχνιδιού ανοιχτό, μουσική κλειστή και χαμηλό ambient. Το UI το ελέγχει και σου το λέει.
- **Pixel (vision/bite.py):** παρακολουθεί ένα μικρό ROI γύρω από το bobber με πάνω από 30 fps. Τσίμπημα θεωρείται όταν το centroid πέφτει πάνω από N px, ή το blob area πέφτει κάτω από το 50%, ή υπάρχει spike στο frame-diff, σε 2 ή περισσότερα διαδοχικά frames.
- **Fusion:** `audio` | `pixel` | `audio OR pixel` | `audio AND pixel`. Επιλέγεται ανά profile, με per-detector thresholds και live γράφημα στο UI.

**Loop και κανόνες**

- **Lures:** λίστα από lures με όνομα, key (π.χ. macro `/use Shiny Bauble` + `/use 16`), διάρκεια και προτεραιότητα. Το lure μπαίνει πριν το επόμενο cast όταν λήξει ο timer, με περιθώριο για το cast time. Το timer ξεκινάει από την εφαρμογή.
- **Timeouts:** `maxWaitMs` ανά cast (η διάρκεια του channel μετριέται στο calibration). Αν δεν υπάρξει τσίμπημα, γίνεται recast.
- **Stop conditions:** διάρκεια session, αριθμός catches, N διαδοχικά misses (pause + ειδοποίηση), χαμένο focus.
- **Προαιρετικό companion addon `FishBridge` (M7):** ένα strip 4 pixels που δείχνει ελεύθερα bag slots, in-combat flag, αν είναι equipped fishing pole και αν υπάρχει lure. Δίνει stop όταν γεμίσουν τα bags και pause όταν σε επιτεθούν. Όλα αυτά διαβάζονται εκτός combat, από μη-secret API. Είναι και το πρώτο δοκιμαστικό του pixel-bridge που θα χρειαστεί το grind bot.

Το state machine του loop:

&#91;embedded content: fishing state machine · 7 states\]

Με audio-only ανίχνευση (modes A και C) το LOCATE παραλείπεται και το CAST περνάει κατευθείαν στο WATCH.

## Fishing bot: UI spec

Το UI είναι ένα παράθυρο με sidebar και 7 οθόνες, dark theme, και ανοίγει πάντα στη δεύτερη οθόνη (θυμάται θέση και μέγεθος). Όλες οι αλλαγές ρυθμίσεων ισχύουν live μέσω WebSocket, χωρίς restart.

| Οθόνη | Περιεχόμενο |
| --- | --- |
| **Dashboard** | Μεγάλα Start / Pause / Stop. Τρέχον state (CAST, WATCH, HOOK…) με χρώμα. Session timer, casts, catches, misses, success %, catches/ώρα. Countdown του lure. Live γράφημα audio level/pixel signal με το threshold. Thumbnail του ROI με το box του bobber. Event log |
| **Mode & Keys** | Επιλογή mode A / B / C με σύντομη εξήγηση και checklist προαπαιτούμενων. Key bindings με «press a key» capture: `fishKey`, `castKey`, `interactKey`. Mouse options: Shift+right-click, επιστροφή του cursor μετά το click |
| **Lures** | Πίνακας: όνομα, key, διάρκεια (λεπτά), cast time, προτεραιότητα, enabled. Toggle auto-lure, περιθώριο επαναφοράς (π.χ. 15s πριν λήξει), κουμπί «Apply now» |
| **Detection** | Μέθοδος (audio / pixel / OR / AND). Sliders για thresholds. Επιλογή audio device. Color presets (κόκκινο/μπλε/custom) με eyedropper πάνω σε screenshot. ROI editor με drag. Κουμπί «Test one cast» |
| **Calibration wizard** | 6 βήματα: επιλογή παραθύρου WoW, ROI, capture bobber template, ηχογράφηση splash + noise floor, μέτρηση land delay και διάρκειας channel, επιβεβαίωση με 5 casts και αποτέλεσμα |
| **Profiles** | Αποθήκευση/φόρτωση ανά σημείο (π.χ. «STV coast — night»), duplicate, import/export JSON. Κάθε profile κρατάει mode, keys, lures, detection, ROI και stop conditions |
| **Settings** | Global hotkeys (Start/Stop toggle, Pause, Emergency stop). Stop conditions (διάρκεια, catches, διαδοχικά misses). Ειδοποιήσεις (Windows toast, προαιρετικά Discord/n8n webhook). Always-on-top, startup monitor |

**Κανόνες σχεδιασμού**

- Το Dashboard χωράει σε μια οθόνη 1080p χωρίς scroll. Το Start/Stop μεγάλο και πάντα ορατό.
- Το χρώμα του state έχει νόημα: πράσινο τρέχει, κίτρινο paused, κόκκινο σφάλμα/στάματησε, γκρι idle.
- Αν σταματήσει από stop condition, βλέπεις τον λόγο («Stopped: 5 misses in a row») και ένα κουμπί «Άνοιξε Detection».
- Κάθε πεδίο έχει tooltip και default τιμή, και υπάρχει «Reset to defaults» ανά οθόνη.
- Η δομή του UI (sidebar, WebSocket client, store, συστατικά) γίνεται έτσι ώστε να προστεθούν αργότερα οι οθόνες του grind bot χωρίς ξαναγράψιμο.

## Fishing bot: milestones για το Claude Code

8 milestones, καθένα με prompt και acceptance criteria. Η σειρά βάζει την ανίχνευση (M2–M3) πριν από το UI, γιατί εκεί είναι το ρίσκο. Το UI χτίζεται πάνω σε engine που ήδη δουλεύει headless.

### M0 — Scaffold και CLAUDE.md

```
Read docs/PLAN.md. Scaffold the AFK repo exactly as in "Repo layout": Python 3.12 package `engine` (uv or pip-tools, ruff, pytest), Vite+React+TS app in `ui/`. Write CLAUDE.md with: stack decisions, folder purposes, coding conventions (type hints, pydantic models, no blocking calls in the asyncio loop), how to run engine/UI/tests, and the rule that detectors must be testable on recorded fixtures without the game running.
```

- [ ] `pytest` και `ruff` τρέχουν πράσινα σε άδειο project
- [ ] Το `npm run dev` στο `ui/` ανοίγει ένα placeholder
- [ ] Το CLAUDE.md περιγράφει stack, εντολές και conventions

### M1 — Capture και input spike

```
Implement engine/capture.py and engine/input.py. Find the WoW window by process name, get its client rect in physical pixels (per-monitor DPI aware), capture an arbitrary ROI with dxcam (fallback mss) at a target fps. Input: SendInput with scancodes for key press/hold/release, mouse move to client-relative coordinates, left/right click, Shift modifier; focus WoW window; detect focus loss. Add `python -m tools.spike` that focuses WoW, presses a configurable key, and saves an ROI screenshot to ./out.
```

- [ ] Το spike βρίσκει το WoW στην οθόνη 1 και το πλήκτρο εκτελείται in-game (π.χ. ανοίγει το bag)
- [ ] Το screenshot του ROI ταιριάζει ακριβώς με την περιοχή του client area, και με διαφορετικό scaling οθονών
- [ ] Το capture πιάνει τουλάχιστον 60 fps σε ROI 400×300

### M2 — Εργαλείο καταγραφής και fixtures

```
Create tools/record_clip.py: while I fish manually, record the ROI at 30 fps plus WASAPI loopback audio (PyAudioWPatch) into tests/fixtures/<timestamp>/ (frames as PNG or mp4, audio as wav, meta.json). Global hotkeys: F8 marks "bobber landed", F9 marks "bite". Write a loader used by tests and tools/replay.py.
```

- [ ] Καταγράφεις τουλάχιστον 20 casts σε 2–3 σημεία (μέρα/νύχτα, θάλασσα/λίμνη)
- [ ] Το `replay.py` αναπαράγει ένα clip με τα markers συγχρονισμένα

### M3 — Detectors

```
Implement vision/bobber.py, vision/bite.py and audio.py per PLAN "Detection". Each detector is a pure class with a config dataclass and a `feed(frame|audio_chunk) -> Event|None` API. Add tools/replay.py metrics: bobber found rate, bite detection latency vs F9 marker, false positives per cast, for each fusion mode. Add pytest tests over all fixtures with thresholds from the acceptance criteria.
```

- [ ] Εντοπισμός bobber στο 95% ή περισσότερο των fixtures
- [ ] Ανίχνευση τσιμπήματος (audio) στο 95% ή περισσότερο, με latency κάτω από 300 ms
- [ ] Λιγότερα από 1 false positive ανά 20 casts

### M4 — State machine, modes και lures (headless)

```
Implement engine/fsm.py with the states and transitions in the PLAN diagram, modes A/B/C, engine/lures.py, stop conditions and engine/stats.py. Add a `--dry-run` flag that logs every key/mouse action instead of sending it, and `python -m engine.run --profile <file>` for a headless session. Unit-test the FSM with fake detectors and a fake clock.
```

- [ ] Headless session 30 λεπτών σε κάθε mode χωρίς crash
- [ ] Το lure ξαναμπαίνει στην ώρα του
- [ ] Μετά από N misses στη σειρά κάνει pause και λέει τον λόγο

### M5 — Engine service, profiles και hotkeys

```
Wrap the engine in engine/server.py (FastAPI + one WebSocket). Commands: start, pause, resume, stop, apply_lure, test_cast, set_config, load/save/list profiles, screenshot_roi. Events: state, stats, detector levels (10 Hz), log, catch. Profiles are JSON validated by pydantic in %APPDATA%/AFK/profiles. Global hotkeys via pynput call the same commands.
```

- [ ] Start/Stop και από hotkey και από WebSocket
- [ ] Η αλλαγή config ενώ τρέχει ισχύει από το επόμενο cast
- [ ] Το emergency stop αφήνει όλα τα πλήκτρα και σταματάει σε λιγότερο από 100 ms

### M6 — UI

```
Build the React UI in ui/ per PLAN "UI spec": sidebar with the 7 screens, Zustand store fed by the WebSocket, shadcn/ui components, dark theme, live detector chart (recharts), ROI preview, key-capture inputs, lures table, profiles manager. Then a pywebview launcher (engine/app.py) that starts the server and opens the UI window on the remembered monitor without stealing focus from WoW while running.
```

- [ ] Όλες οι ρυθμίσεις του PLAN είναι στο UI και αποθηκεύονται στο profile
- [ ] Το Dashboard δείχνει state, stats και γράφημα live
- [ ] Το παράθυρο ανοίγει στην οθόνη 2 και δεν κλέβει focus από το WoW

### M7 — Calibration, FishBridge και packaging

```
Implement the 6-step calibration wizard (UI + engine endpoints) that writes its results into the active profile. Create the optional Lua addon FishBridge (Interface 16001): a 4-pixel strip encoding free bag slots, in-combat flag, fishing pole equipped, lure active; add its decoder to the engine and stop/pause rules. Package with PyInstaller (one-folder) and write README with setup steps for each mode.
```

- [ ] Μετά το wizard, 50 casts με 90% ή περισσότερο success σε νέο σημείο
- [ ] Με το FishBridge: stop όταν γεμίσουν τα bags, pause όταν μπεις σε combat
- [ ] Το `.exe` τρέχει σε καθαρό φάκελο χωρίς εγκατεστημένο Python

## Κίνδυνοι, ανοιχτά ερωτήματα, πηγές

| Κίνδυνος | Επίπτωση | Αντιμετώπιση |
| --- | --- | --- |
| Account action (το automation απαγορεύεται από τους όρους χρήσης) | Χάνεται το account | Δική σου απόφαση ποιο account θα χρησιμοποιήσεις |
| Ρυθμίσεις γραφικών/UI scale αλλάζουν | Σπάει το vision | Το calibration αποθηκεύει ανάλυση/scale στο profile και σε αλλαγή ζητάει επαναληπτικό calibration |
| Θόρυβος στο audio (Discord, μουσική) | False positives | Loopback μόνο από τη διεργασία του WoW (process loopback, Windows 11) ή fusion `audio AND pixel` |
| Το focus φεύγει από το WoW | Πλήκτρα πάνε αλλού | Auto-pause όταν χαθεί το focus, το UI δεν κλέβει focus όσο τρέχει |

**Ανοιχτά ερωτήματα (ελέγχονται in-game)**

- [ ] Διάρκεια του fishing channel στο Forever (μετρήσεις στο calibration)
- [ ] Δουλεύει το `PostMessage` input με το WoW στο background;

**Πηγές**

- [EzFishing Forever](https://www.curseforge.com/wow/addons/ezfishing-forever): soft-target interact στο Forever
- [Campfire Angler](https://www.curseforge.com/wow/addons/campfire-angler): το τσίμπημα δεν φαίνεται στα addons
- [MacroPilot Forever](https://www.curseforge.com/wow/addons/macropilot-forever): interface 16001 / game 1.60.1
- [Patch 1.60.1 API changes — notes](https://warcraft.wiki.gg/wiki/Patch_1.60.1/API_changes/notes): το Forever είναι Mainline με secret values
