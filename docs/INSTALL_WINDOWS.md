# Installing and running AKF on Windows

A step-by-step guide for people who have never used a terminal. It takes about 15 minutes.
On a Mac, follow [INSTALL_MACOS.md](INSTALL_MACOS.md) instead.

> Automating the game is against Blizzard's terms of service and can cost you the account.
> Whether and where to use this is your decision.

## What you need

- Windows 10 (version 2004 or later) or Windows 11.
- World of Warcraft Forever, installed and working.
- An internet connection for the installation.
- A one-button fishing addon in WoW (one key casts, the same key reels in). The bot only
  presses that key; the default it expects is `]`.

You will install three free programs: **Git** (downloads the code), **uv** (installs Python
and everything the bot needs) and **Node.js** (builds the control panel). You do **not** need
to install Python yourself: uv installs the right version (3.12) for you.

---

## Step 1: Open PowerShell

Every command in this guide is typed into **PowerShell**, a text window that runs commands.

1. Press the **Windows key**, type `PowerShell` and press **Enter**.
2. A dark blue or black window opens. That is where you paste commands.

How to paste a command: copy it from this page, click inside PowerShell, **right-click** (or
press **Ctrl+V**), then press **Enter**. Wait until the blinking cursor comes back before
the next command.

> Do not use "Run as administrator" unless step 9 tells you to.

## Step 2: Install Git

Paste this into PowerShell and press Enter:

```powershell
winget install --id Git.Git -e --source winget
```

Accept anything it asks with **Y** and Enter. When it says it is done, **close PowerShell
and open it again** (step 1), so it finds the new program.

<details>
<summary>"winget is not recognized"?</summary>

Your Windows is missing the App Installer. Download Git from
<https://git-scm.com/download/win> instead, run the installer and click **Next** on every
screen (the defaults are fine). Then reopen PowerShell.
</details>

Check it worked:

```powershell
git --version
```

It should print something like `git version 2.47.1.windows.1`.

## Step 3: Install uv (it brings Python)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

When it finishes, **close PowerShell and open it again**, then check:

```powershell
uv --version
```

It should print something like `uv 0.9.x`.

## Step 4: Install Node.js

```powershell
winget install --id OpenJS.NodeJS.LTS -e --source winget
```

(Or download the **LTS** installer from <https://nodejs.org> and click through it.) Then
**close and reopen PowerShell**, and check:

```powershell
node --version
```

It must print `v22` or higher (for example `v22.12.0`).

## Step 5: Download AKF

This puts the code in a folder called `AKF` inside your Documents:

```powershell
cd $HOME\Documents
git clone https://github.com/alexkollan/AKF.git AKF
cd AKF
```

> If the repository is private, the owner has to invite your GitHub account first. Git will
> then open a browser window asking you to sign in to GitHub; do so and the download
> continues.

**From now on, every command is run inside this folder.** If you close PowerShell, open it
again and first type:

```powershell
cd $HOME\Documents\AKF
```

## Step 6: Install the bot's parts

Three commands, one after the other. The first one downloads Python 3.12 and every library
the bot uses; the other two build the control panel. Each can take a few minutes.

```powershell
uv sync
```

```powershell
npm --prefix ui install
```

```powershell
npm --prefix ui run build
```

The last one ends with a line like `✓ built in 2.1s`.

<details>
<summary>"running scripts is disabled on this system" when running npm?</summary>

Windows blocks npm's start-up script by default on some PCs. Run this once, answer **Y**,
then repeat the npm command:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```
</details>

## Step 7: Set up WoW

In the game:

1. **Sound:** Options → Audio. **Sound Effects on**, **Music off** (ambience may stay on).
   The bot hears the splash of the bite, so it needs the game's sound.
   Turn on **Sound in Background** if you want it to keep listening while another window is
   in front for a moment.
2. **Auto Loot:** Options → Controls → **Auto Loot** on.
3. **Fishing key:** in your one-button fishing addon, bind its key to `]` (or note the key
   you use; you can tell the bot in step 8).

Optional but recommended: install the **FishBridge** addon, which lets the bot stop when
your bags are full and pause when something attacks you. With WoW running:

```powershell
uv run python -m tools.install_addon
```

Then type `/reload` in the game chat.

## Step 8: Start AKF

```powershell
uv run python -m engine.app
```

The AKF control panel opens (on your second monitor, if you have one).

1. Open **Mode & keys**, choose **One-button addon** (mode A) and check that the **Fishing key**
   matches your addon's key.
2. Stand at the water in WoW with your fishing pole equipped.
3. Press **Start fishing** in the panel. WoW comes to the front and the bot takes over.

While WoW is in front, these keys control the bot (WoW does not see them while AKF runs):

| Key | Does |
| --- | --- |
| **F9** | Start or stop |
| **F10** | Pause or resume |
| **F12** | Emergency stop: lets go of every key at once |

Clicking out of WoW pauses the bot by itself. To close AKF, close its window, or press
**Ctrl+C** in PowerShell.

**Try it safely first:** start with `uv run python -m engine.app --dry-run`. The bot then
does everything except press keys, and writes every key it would have pressed to the log.

## Step 9: If something does not work

- **The bot casts but never reels in.** Check the game's Sound Effects are on and not
  muted. In the panel, lower Detection → Sensitivity, or run the sound step in
  **Calibration**.
- **It reels in when nothing bit.** Raise Sensitivity. Waves, rain and other players'
  spells at busy spots are the usual cause.
- **Keys do nothing in WoW, and the log says "SendInput delivered 0/1 events".** WoW is
  running as administrator, so AKF has to as well: open PowerShell with right-click →
  **Run as administrator**, `cd $HOME\Documents\AKF`, and start it again.
- **The window opens empty or white.** The control panel was not built: repeat the two npm
  commands from step 6. If it is still empty, install Microsoft's WebView2 Runtime from
  <https://developer.microsoft.com/microsoft-edge/webview2/>.
- **"no window found for process"**: WoW is not running, or runs under another name. See
  what is open with `uv run python -m tools.spike --list`.
- **To watch exactly what the bot hears**, fish by hand and run
  `uv run python -m tools.listen --bot`; it prints BITE whenever it hears one.

## Updating to a newer version

```powershell
cd $HOME\Documents\AKF
git pull
uv sync
npm --prefix ui install
npm --prefix ui run build
```

Your settings and profiles are kept: they live in `%APPDATA%\AKF`, not in the AKF folder.
