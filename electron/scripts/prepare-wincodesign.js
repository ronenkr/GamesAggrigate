// Pre-extracts the electron-builder winCodeSign cache to work around the
// "Cannot create symbolic link : A required privilege is not held by the client"
// error on Windows machines without admin rights or Developer Mode enabled.
//
// The winCodeSign-2.6.0.7z archive contains two darwin/.dylib symlinks that
// 7zip cannot create without SeCreateSymbolicLinkPrivilege. electron-builder
// treats the resulting exit code 2 as a hard failure and re-downloads forever.
//
// This script:
//   1. Downloads winCodeSign-2.6.0.7z into the electron-builder cache if missing.
//   2. Extracts it with -snl- (do not restore symlinks) using the bundled 7za.exe.
//   3. Tolerates non-zero exit codes (the symlink errors are expected).
//   4. Leaves the cache in the exact layout electron-builder expects so it skips
//      its own download/extract step.

const fs = require("node:fs");
const path = require("node:path");
const https = require("node:https");
const { spawnSync } = require("node:child_process");

const WIN_CODE_SIGN_VERSION = "2.6.0";
const ARCHIVE_NAME = `winCodeSign-${WIN_CODE_SIGN_VERSION}.7z`;
const DOWNLOAD_URL = `https://github.com/electron-userland/electron-builder-binaries/releases/download/winCodeSign-${WIN_CODE_SIGN_VERSION}/${ARCHIVE_NAME}`;

const localAppData = process.env.LOCALAPPDATA;
if (!localAppData) {
  console.error("LOCALAPPDATA env var not set; this script only runs on Windows.");
  process.exit(1);
}

const cacheRoot = path.join(localAppData, "electron-builder", "Cache", "winCodeSign");
const targetDir = path.join(cacheRoot, `winCodeSign-${WIN_CODE_SIGN_VERSION}`);
const archivePath = path.join(cacheRoot, ARCHIVE_NAME);
const sevenZip = path.join(__dirname, "..", "..", "node_modules", "7zip-bin", "win", "x64", "7za.exe");

function download(url, dest) {
  return new Promise((resolve, reject) => {
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    const tmp = `${dest}.part`;
    const file = fs.createWriteStream(tmp);
    const handle = (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        https.get(res.headers.location, handle).on("error", reject);
        return;
      }
      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode} from ${url}`));
        return;
      }
      res.pipe(file);
      file.on("finish", () => {
        file.close((err) => {
          if (err) {
            reject(err);
            return;
          }
          fs.renameSync(tmp, dest);
          resolve();
        });
      });
    };
    https.get(url, handle).on("error", reject);
  });
}

function isCachePopulated() {
  if (!fs.existsSync(targetDir)) {
    return false;
  }
  // electron-builder needs the Windows signtool.exe to exist for cache to be considered valid.
  const signtool = path.join(targetDir, "windows-10", "x64", "signtool.exe");
  return fs.existsSync(signtool);
}

(async () => {
  if (isCachePopulated()) {
    console.log(`[prepare-wincodesign] Cache already populated at ${targetDir}`);
    return;
  }

  fs.mkdirSync(cacheRoot, { recursive: true });

  if (!fs.existsSync(archivePath)) {
    console.log(`[prepare-wincodesign] Downloading ${DOWNLOAD_URL}`);
    await download(DOWNLOAD_URL, archivePath);
    console.log(`[prepare-wincodesign] Downloaded to ${archivePath}`);
  }

  if (!fs.existsSync(sevenZip)) {
    console.error(`[prepare-wincodesign] 7za.exe not found at ${sevenZip}; run npm install first.`);
    process.exit(1);
  }

  // Clean any half-extracted dir from a previous failed run.
  if (fs.existsSync(targetDir)) {
    fs.rmSync(targetDir, { recursive: true, force: true });
  }
  fs.mkdirSync(targetDir, { recursive: true });

  console.log(`[prepare-wincodesign] Extracting (ignoring symlink errors)...`);
  const result = spawnSync(
    sevenZip,
    ["x", "-y", "-bd", `-o${targetDir}`, archivePath],
    { stdio: ["ignore", "pipe", "pipe"], encoding: "utf8" },
  );

  // Exit code 2 with "Cannot create symbolic link" is expected and harmless for Windows builds.
  const stderr = String(result.stderr || "");
  const onlySymlinkErrors = /Cannot create symbolic link/i.test(stderr) && !/ERROR:(?!.*symbolic link)/i.test(stderr);

  if (result.status !== 0 && !onlySymlinkErrors) {
    console.error("[prepare-wincodesign] 7za extraction failed:");
    console.error(stderr);
    process.exit(result.status || 1);
  }

  if (!isCachePopulated()) {
    console.error(`[prepare-wincodesign] Extraction completed but signtool.exe missing. Looked at: ${targetDir}`);
    console.error("stdout:", result.stdout);
    console.error("stderr:", stderr);
    process.exit(1);
  }

  console.log(`[prepare-wincodesign] Cache ready at ${targetDir}`);
})().catch((err) => {
  console.error("[prepare-wincodesign] Failed:", err && err.message ? err.message : err);
  process.exit(1);
});
