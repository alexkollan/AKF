# Installing and running AKF on a Mac

A step-by-step guide for people who have never used a terminal. It takes about 15 minutes.
On Windows, follow [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) instead.

> Automating the game is against Blizzard's terms of service and can cost you the account.
> Whether and where to use this is your decision.

## What you need

- macOS 13 Ventura or later (tested on macOS 26 with Apple silicon).
- World of Warcraft Forever, installed through Battle.net and working (the native Mac
  version; nothing else is needed).
- An internet connection for the installation.
- A one-button fishing addon in WoW (one key casts, the same key reels in). The bot only
  presses that key; the default it expects is `]`.

You will install three free programs: **Git** (downloads the code), **uv** (installs Python
and everything the bot needs) and **Node.js** (builds the control panel). You do **not** need
to install Python yourself: uv installs the right version (3.12) for you.

---

## Step 1: Open Terminal

Every command in this guide is typed into **Terminal**, a text window that runs commands.

1. Press **⌘ Command + Space**, type `Terminal` and press **Return**.
2. A window with a blinking cursor opens. That is where you paste commands.

How to paste a command: copy it from this page, click inside Terminal, press **⌘ Command+V**,
then **Return**. Wait until the cursor comes back before the next command. If Terminal asks
for your password, type your Mac login password (nothing appears while you type; that is
normal) and press Return.

## Step 2: Install Git

```bash
xcode-select --install
```

A window appears asking to install the **command line developer tools**. Click **Install**,
accept the licence and wait (this can take 5-10 minutes). If Terminal instead says they are
"already installed", Git is already there.

Check it worked:

```bash
git --version
```

It should print something like `git version 2.39.5 (Apple Git-154)`.

## Step 3: Install uv (it brings Python)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

When it finishes, **quit Terminal (⌘ Command+Q) and open it again**, then check:

```bash
uv --version
```

It should print something like `uv 0.9.x`.

## Step 4: Install Node.js

1. Open <https://nodejs.org> and download the **LTS** version for macOS (the `.pkg` file).
2. Open the downloaded file and click **Continue** / **Install** through the installer.
3. **Quit Terminal and open it again**, then check:

```bash
node --version
```

It must print `v22` or higher (for example `v22.12.0`).

> If you use Homebrew, `brew install uv node` does steps 3 and 4 in one go.

## Step 5: Download AKF

This puts the code in a folder called `AKF` inside your Documents:

```bash
cd ~/Documents
git clone https://github.com/alexkollan/AKF.git AKF
cd AKF
```

> If the repository is private, the owner has to invite your GitHub account first, and Git
> will ask for a username and password. GitHub no longer accepts your account password
> there; the easiest way is to install **GitHub Desktop** (<https://desktop.github.com>),
> sign in, choose **File → Clone Repository**, pick `alexkollan/AKF` and set the local path
> to `Documents/AKF`. Then continue with `cd ~/Documents/AKF` in Terminal.

**From now on, every command is run inside this folder.** If you close Terminal, open it
again and first type:

```bash
cd ~/Documents/AKF
```

## Step 6: Install the bot's parts

Three commands, one after the other. The first one downloads Python 3.12 and every library
the bot uses; the other two build the control panel. Each can take a few minutes.

```bash
uv sync
```

```bash
npm --prefix ui install
```

```bash
npm --prefix ui run build
```

The last one ends with a line like `✓ built in 2.1s`.

## Step 7: Give Terminal the permissions it needs

macOS only lets an app see the screen, hear another app and press keys if you allow it. The
permissions go to **Terminal** (the app that runs AKF), not to AKF itself.

1. Run:

   ```bash
   uv run python -m tools.permissions --request
   ```

   macOS shows a few pop-ups. For each one, click **Open System Settings**.
2. In **System Settings → Privacy & Security**, turn **Terminal** on in all three of:
   - **Screen & System Audio Recording** (to hear the game),
   - **Accessibility** (to press keys in the game),
   - **Input Monitoring** (for the F9 / F10 / F12 keys).

   If Terminal is not in a list, click the **+** button under it, open **Applications →
   Utilities**, choose **Terminal** and click **Open**.
3. **Quit Terminal (⌘ Command+Q) and open it again.** Permissions only take effect after a
   restart.
4. Check again (after `cd ~/Documents/AKF`):

   ```bash
   uv run python -m tools.permissions
   ```

   **Screen Recording**, **Accessibility** and **Input Monitoring** must all say `ok`.
   "Function keys" may say MISSING; see the next point.

**Function keys.** On a Mac keyboard, F9 / F10 / F12 change the volume unless you hold the
**fn** key (🌐 on newer keyboards). Either hold **fn** whenever you press them for the bot,
or turn on System Settings → Keyboard → Keyboard Shortcuts → Function Keys → **Use F1, F2,
etc. keys as standard function keys**.

> If you run AKF from another app's terminal (an editor, or the Claude app), that app needs
> the permissions instead of Terminal.

## Step 8: Set up WoW

In the game:

1. **Display:** Options → Graphics → Display Mode **Windowed (Fullscreen)** or Fullscreen.
   A small window with a title bar works too, but fullscreen is what has been tested.
2. **Sound:** Options → Audio. **Sound Effects on**, **Music off** (ambience may stay on).
   The bot hears the splash of the bite, so it needs the game's sound.
   Turn on **Sound in Background** if you want it to keep listening while another window is
   in front for a moment.
3. **Auto Loot:** Options → Controls → **Auto Loot** on.
4. **Fishing key:** in your one-button fishing addon, bind its key to `]` (or note the key
   you use; you can tell the bot in step 9).
5. **Free the F8-F12 keys.** On a Mac, WoW reads the keyboard directly, so it always sees
   F9 / F10 / F12 even while the bot uses them, and by default those keys open your bags.
   To stop that, type this in the game chat (out of combat) and press Return:

   ```
   /run for _,k in ipairs({"F8","F9","F10","F11","F12"}) do SetBinding(k) end SaveBindings(GetCurrentBindingSet())
   ```

   Your bags still open with **B**. (You can also do it by hand: Options → Keybindings →
   Interface Panel, and clear Toggle Backpack and the four Toggle Bag keys.) WoW stores
   key bindings with your account, so this also applies on your other computers.

Optional but recommended: install the **FishBridge** addon, which lets the bot stop when
your bags are full and pause when something attacks you. With WoW running:

```bash
uv run python -m tools.install_addon
```

Then type `/reload` in the game chat.

## Step 9: Start AKF

```bash
uv run python -m engine.app
```

The AKF control panel opens.

1. Open **Mode & keys**, choose **One-button addon** (mode A) and check that the **Fishing key**
   matches your addon's key.
2. Stand at the water in WoW with your fishing pole equipped.
3. Press **Start fishing** in the panel. WoW comes to the front and the bot takes over.

While WoW is in front, these keys control the bot (hold **fn** unless you changed the
setting in step 7):

| Key | Does |
| --- | --- |
| **F9** | Start or stop |
| **F10** | Pause or resume |
| **F12** | Emergency stop: lets go of every key at once |

Switching away from WoW (⌘ Command+Tab) pauses the bot by itself. To close AKF, close its
window, or press **Control+C** in Terminal.

**Try it safely first:** start with `uv run python -m engine.app --dry-run`. The bot then
does everything except press keys, and writes every key it would have pressed to the log.

## Step 10: If something does not work

- **"MISSING" permissions, or "this process is not allowed to control the computer".**
  Step 7 again: the permission must be on for the app you type the commands in, and that
  app must have been restarted afterwards.
- **F9 / F10 / F12 change the volume instead.** Hold **fn**, or turn on standard function
  keys (step 7).
- **A bag opens when you press F9 / F10 / F12.** Step 8.5 was not done, or was undone.
- **The bot casts but never reels in.** Check the game's Sound Effects are on and not
  muted. In the panel, lower Detection → Sensitivity, or run the sound step in
  **Calibration**.
- **It reels in when nothing bit.** Raise Sensitivity. Waves, rain and other players'
  spells at busy spots are the usual cause.
- **The window opens empty.** The control panel was not built: repeat the two npm commands
  from step 6.
- **"no window found for process"**: WoW is not running. See what is open with
  `uv run python -m tools.spike --list`.
- **To watch exactly what the bot hears**, fish by hand and run
  `uv run python -m tools.listen --bot`; it prints BITE whenever it hears one.

## Updating to a newer version

```bash
cd ~/Documents/AKF
git pull
uv sync
npm --prefix ui install
npm --prefix ui run build
```

Your settings and profiles are kept: they live in `~/.config/AKF`, not in the AKF folder.
