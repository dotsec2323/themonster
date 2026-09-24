# Building CanFallingDetector.exe — No Python, No Command Line

This builds the real Windows `.exe` for you automatically, on a temporary
Windows machine provided by GitHub. Your PC and the production PC never
run Python, pip, or PyInstaller — only GitHub's cloud runner does, and it's
destroyed after every build.

## One-time setup (about 5 minutes)

**1. Create a free GitHub account**
Go to https://github.com/signup if you don't already have one.

**2. Create a new repository**
- Click the **+** icon (top right) → **New repository**
- Name it e.g. `can-falling-detector`
- Choose **Private** (recommended, since this is internal plant software) or Public
- Leave everything else default → click **Create repository**

**3. Upload the project files**
- On the new repo's page, click **"uploading an existing file"** (or "Add file" → "Upload files")
- Unzip `CanFallingDetector_Stage1.zip` on your PC first
- Drag the **entire contents** of the unzipped `CanFallingDetector` folder into
  the browser upload area (including the hidden `.github` folder — if your
  file explorer hides it, enable "show hidden items" in Windows Explorer's
  View menu, or drag the whole extracted folder in one go)
- Scroll down, click **"Commit changes"**

## Every time you want to build (or rebuild) the .exe

**4. Go to the "Actions" tab** at the top of your repository page.

You should see a workflow called **"Build Windows EXE"** already running
(it starts automatically after step 3, and again after every future upload).
If it's not running, click on it, then click **"Run workflow"** → **"Run workflow"**
(green button) to trigger it manually.

**5. Wait for it to finish**
Takes roughly 10–20 minutes (installing torch and packaging is slow).
A green checkmark ✅ means success; a red ✗ means it failed — click into it
and send me the error text if that happens.

**6. Download the built application**
- Click into the finished (green ✅) workflow run
- Scroll down to **"Artifacts"**
- Click **"CanFallingDetector-Windows"** — this downloads a `.zip`

**7. Deploy to the production PC**
- Unzip it — you'll get a folder containing `CanFallingDetector.exe` plus
  its required files (DLLs, bundled model, etc.)
- Copy the **whole folder** (not just the .exe) to the production PC
- Double-click `CanFallingDetector.exe` to run it — no installation needed

## If Windows SmartScreen blocks it on the production PC
Since this is a freshly-built, unsigned executable, Windows may show a blue
"Windows protected your PC" warning the first time. Click **"More info"** →
**"Run anyway"**. This is expected for internal tools and not a sign of a
problem with the app.

## What happens on every future update
Whenever I give you new code (Stage 2, 3, etc.), you'll re-upload the
changed files to the same GitHub repository the same way (drag and drop),
and step 4 onward repeats — no local install ever required.
