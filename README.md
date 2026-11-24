# LapGuard

A lightweight laptop anti-theft alarm for Windows 11 and Linux. Arm it with a hotkey before you walk away. If anyone touches the laptop (keyboard, unplugging power, closing the lid, moving it, plugging something in, trying to shut down), it sounds a siren and shows a lock screen with your photo until the correct passkey is entered.

## Status

| Milestone | Windows 11 | Linux |
|---|---|---|
| 1. Core state machine, config, passkey, journal, event bus, logging | **done, tested** | untested (pure Python, expected to work) |
| 2. Platform adapter: keyboard, power, shutdown, lid, devices | **done, unit/integration tested; awaiting your hands-on test** | not started |
| 3. Siren, volume, lock screen | not started | not started |
| 4. Camera sensor | not started | not started |
| 5. Tray, hotkey, shortcut, packaging | not started | not started |
| Phase 2: ntfy alerts and remote control | designed only (config section exists) | designed only |

A feature is only marked as working on an OS after it has been run on that OS.

## Setup (development)

```powershell
# Windows
cd lapguard
py -3.12 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest -q
```

```bash
# Linux (untested so far)
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest -q
```

Platform-specific dependencies are selected automatically: pycaw on Windows, and evdev, dbus-next and pulsectl on Linux.

## Commands

| Command | What it does |
|---|---|
| `lapguard --diag` | Prints raw OS events (lid, AC, devices, audio, session lock, shutdown queries). Arms and changes nothing. |
| `lapguard --dev --arm` | A real armed session in the console: keyboard captured, sensors and protections on. Auto-disarms after 60 s. Until the tray exists (milestone 5), `--arm` requires `--dev`. |
| `lapguard --set-passkey` | Set or change the passkey. Changing it asks for the current one first. |
| `lapguard --simulate [--dev]` | Console simulation of the alarm logic. It installs no hooks and touches nothing on the system. |
| `lapguard --restore` | Restores every system setting LapGuard changed, then exits. Safe to run at any time. |
| `lapguard --dev` | Safe test mode: auto-disarms 60 s after arming, shows a DEV badge, and doesn't force volume. |
| `lapguard --arm` | Arms directly; for desktop shortcuts. *(milestone 5)* |

## How it behaves

```
DISARMED -> ARMING (countdown, 10 s) -> ARMED -> TRIGGERED (grace window, 8 s) -> ALARM
    ^--------------- correct passkey + Enter (or a forced disarm, see Recovery) ------|
```

**Keyboard and passkey entry**
- While armed, any keypress opens the grace window and the passkey prompt.
- Either type the passkey straight away and press Enter, or press a key first to wake the prompt (Space, Shift, anything) and then type it. Both are accepted on the first try, so a wake key never costs you an attempt.
- Backspace edits and Esc clears. An Enter with nothing typed costs nothing.

**Wrong passkey, timeout, or immediate sensors**
- A wrong passkey counts as a failed attempt. Reaching `max_attempts` (default 2) sounds the alarm, and so does letting the grace window run out.
- Some sensors skip the grace window and go straight to ALARM. By default these are shutdown or logoff attempts, lid close, session lock or resume from sleep, and any device being plugged in or removed. Configure this per sensor with `immediate = true|false`.

**During the alarm**
- The alarm never times out. Only the correct passkey stops it.

**What gets logged**
- Every event is logged with a timestamp to `lapguard.log` in the state directory.
- Typed characters and the passkey length are never logged.

## What LapGuard changes on Windows while armed

Everything runs as a normal user; **no admin rights are needed**. Every change is written to the restore journal *before* it is made.

| When | Change | Undone |
|---|---|---|
| Arming starts | Keeps the PC and display awake (`SetThreadExecutionState`) | On disarm or exit (dies with the process) |
| Arming starts | Lid, power-button and sleep-button actions set to **Do nothing** (AC and battery) on the active power plan | Journal restore |
| Arming starts | `HKCU\...\RunOnce\!LapGuardRestore` = `lapguard --restore`, so even a crash followed by a reboot restores everything | Journal restore (or Windows runs it once) |
| Arming starts, optional | `DisableTaskMgr` / `DisableLockWorkstation` policies (HKCU). Off by default, and never applied in `--dev` | Journal restore |
| Armed | All keys captured by a low-level keyboard hook | On disarm, process exit, or the dev self-release timer |
| Armed | Shutdown and logoff vetoed, with a reason shown by Windows | On disarm |

**Sensors while armed (Windows)**

| Sensor | Source | Default |
|---|---|---|
| Keyboard | `WH_KEYBOARD_LL` hook. Tracks Shift, CapsLock and AltGr itself, because swallowed keys never update Windows' key state | grace window |
| AC unplugged | `GUID_ACDC_POWER_SOURCE` notification | grace window |
| Lid closed | `GUID_LIDSWITCH_STATE_CHANGE`; works even with the lid action set to "Do nothing" | immediate alarm |
| Shutdown / logoff | `WM_QUERYENDSESSION` (vetoed), plus a notification when the system suspends | immediate alarm |
| Session lock / resume | Win+L (`WTS_SESSION_LOCK`), resume from sleep | immediate alarm |
| Devices | `WM_DEVICECHANGE` for all device classes (USB, HID, storage, monitors...), plus audio endpoints (headphones, Bluetooth audio, default output). One plug collapses to one event | immediate alarm |

## Files

| What | Windows | Linux |
|---|---|---|
| Config (TOML) | `%APPDATA%\LapGuard\config.toml` | `~/.config/lapguard/config.toml` (mode 0600) |
| Log, restore journal | `%LOCALAPPDATA%\LapGuard\` | `~/.local/state/lapguard/` |
| Emergency disarm file | `%USERPROFILE%\.lapguard\DISARM` | `~/.lapguard/DISARM` |

**About the config file**
- The config is created when you first set a passkey.
- Every key is optional; anything missing uses its default.
- Unknown keys are logged as warnings, and invalid values stop startup with a message naming the key.
- The passkey is stored only as an **argon2id** hash.
- Passkeys are limited to printable ASCII, because they are typed through a raw keyboard hook. ASCII is the only range that translates reliably across keyboard layouts.

## Recovery: if something goes wrong

LapGuard is designed so that **you never need to reboot to recover**. In order of preference:

1. **Type the passkey.** Press any key, type the passkey, then press Enter.
2. **Emergency file.** Create the file `~/.lapguard/DISARM` from anywhere you can, such as an SSH session. LapGuard force-disarms within about 0.5 s and deletes the file.
   - Windows: `type nul > %USERPROFILE%\.lapguard\DISARM`
   - Linux: `touch ~/.lapguard/DISARM`
3. **Dev mode.** Under `--dev`, LapGuard disarms itself 60 s after arming, whatever happens. Always test new builds this way.
4. **Kill the process.** On Windows, press Ctrl+Alt+Del and open Task Manager to end LapGuard (dev mode never disables Task Manager). On Linux, run `pkill -f lapguard` from another TTY or over SSH. Ending the process releases the keyboard hook or evdev grab immediately.
5. **Restore system settings.** Run `lapguard --restore`. Every setting LapGuard changes (lid and power-button actions, policy keys, volume) is written to a journal *before* it is changed. The journal is replayed on disarm, on clean exit, at the next start after a crash, and whenever you run this command.

**Forgotten passkey:** while disarmed, open the config file and delete the `hash = "..."` line under `[passkey]`. Then run `lapguard --set-passkey`.

**Corrupt journal:** it is moved aside as `journal.corrupt-<time>.json` and never silently deleted. Open it to see which original values to restore by hand.

## Known limitations

These are filled in per milestone as each one is verified. For Windows:
- **Ctrl+Alt+Del** cannot be intercepted by any program. From that screen, a thief can reach the power menu. LapGuard vetoes the shutdown and alarms, but Windows then shows its own "apps are preventing shutdown" screen with a **Shut down anyway** button, which no app can hide.
- **Holding the power button for about 4 s** forces power-off in hardware.
- **Sleep can't be vetoed** on this Modern Standby laptop. LapGuard alarms when Windows reports the suspend or the resume.
- **Fn volume keys** on some laptops are handled by vendor firmware or services rather than the keyboard driver, so the hook can't swallow them. Volume is re-enforced during the alarm (milestone 3).
- **Headphones:** if the audio driver switches the headphone jack inside the same "Speakers" device, no device event is raised. Run `lapguard --diag` and plug headphones in to see what this laptop reports.
- **Win+L** can't be blocked by the hook. By default, locking the session counts as an alarm trigger.
- **The keyboard hook is skipped while Windows shows a secure screen** (Ctrl+Alt+Del, the UAC prompt, the lock screen). This is by design in Windows.
