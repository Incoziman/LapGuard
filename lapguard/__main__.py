"""Command-line entry point.

    lapguard                 start the app (tray UI arrives in milestone 5)
    lapguard --dev --arm     console session: arm for real (keyboard captured, sensors on),
                             auto-disarm after 60 s. Until the tray exists, --arm needs --dev.
    lapguard --dev-loud      like --dev but volume forcing is allowed       [milestone 3]
    lapguard --diag          print raw OS events (lid, AC, devices, session...) - changes nothing
    lapguard --set-passkey   set / change the passkey (stored as an argon2id hash)
    lapguard --restore       restore every system setting recorded in the journal, then exit
    lapguard --simulate      console simulation of the state machine (no OS hooks)
"""

from __future__ import annotations

import argparse
import getpass
import logging
import queue
import sys
import threading
import time

from . import __version__
from .core import config as cfgmod
from .core.journal import Journal, RestoreReport
from .core.log import setup_logging

log = logging.getLogger("lapguard")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lapguard", description="Laptop anti-theft alarm.")
    p.add_argument("--version", action="version", version=f"lapguard {__version__}")
    p.add_argument("--arm", action="store_true", help="arm immediately (starts the app if needed)")
    p.add_argument("--dev", action="store_true", help="dev mode: auto-disarm after 60 s, DEV badge")
    p.add_argument("--dev-loud", action="store_true", help="dev mode that may force system volume")
    p.add_argument("--set-passkey", action="store_true", help="set or change the passkey")
    p.add_argument("--restore", action="store_true", help="restore journaled system settings and exit")
    p.add_argument("--simulate", action="store_true", help="interactive console simulation (no OS hooks)")
    p.add_argument("--diag", action="store_true", help="print raw OS events without arming or changing anything")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dev = args.dev or args.dev_loud
    try:
        cfg, warnings = cfgmod.load_config()
    except cfgmod.ConfigError as e:
        print(f"config error: {e}\nfile: {cfgmod.config_path()}", file=sys.stderr)
        return 2
    log_path = setup_logging(cfgmod.state_dir(), "DEBUG" if dev else cfg.general.log_level, console=dev)
    for w in warnings:
        log.warning("config: %s", w)

    # Crash recovery always runs first: a leftover journal means the last run died armed.
    report = startup_restore()

    if args.restore:
        _print_report(report)
        return 0 if report.ok else 1
    if args.set_passkey:
        return cmd_set_passkey(cfg)
    if args.simulate:
        return cmd_simulate(cfg, dev=dev)
    if args.diag:
        return cmd_diag(cfg)
    if args.arm:
        if not dev:
            print("Until the tray app exists (milestone 5), arming from the console requires --dev.",
                  file=sys.stderr)
            return 2
        return cmd_console_session(cfg, dev=True)

    print("LapGuard: the tray app arrives in milestone 5. For now:")
    print("  lapguard --diag            watch raw lid / AC / device / session events (changes nothing)")
    print("  lapguard --dev --arm       arm for real in the console, auto-disarms after 60 s")
    print(f"log: {log_path}")
    return 0


# ---------------------------------------------------------------------- restore

def startup_restore() -> RestoreReport:
    from .platform import register_restorers

    journal = Journal(cfgmod.journal_path())
    register_restorers(journal)
    if not journal.has_pending() and journal.load_error is None:
        return RestoreReport()
    log.warning("previous run left %d journaled change(s); restoring", len(journal))
    report = journal.restore_all()
    if not report.ok:
        log.error("restore incomplete: %s", report)
    return report


def _print_report(r: RestoreReport) -> None:
    if not (r.restored or r.failed or r.skipped or r.load_error):
        print("Nothing to restore - no system settings are currently modified by LapGuard.")
        return
    for item in r.restored:
        print(f"restored  {item}")
    for item, err in r.failed:
        print(f"FAILED    {item}: {err}")
    for item in r.skipped:
        print(f"PENDING   {item} (no restorer available in this build)")
    if r.load_error:
        print(f"ERROR     {r.load_error}")


# ---------------------------------------------------------------------- passkey

def cmd_set_passkey(cfg: cfgmod.Config) -> int:
    from .core.passkey import PasskeyError, hash_passkey, verify_passkey

    if cfg.has_passkey:
        current = getpass.getpass("Current passkey: ")
        if not verify_passkey(cfg.passkey.hash, current):
            print("Wrong passkey. (Forgot it? See README > Recovery > Forgotten passkey.)", file=sys.stderr)
            return 1
    print("Printable ASCII only (layout-independent), at least 4 characters.")
    new = getpass.getpass("New passkey: ")
    if getpass.getpass("Repeat new passkey: ") != new:
        print("Passkeys do not match.", file=sys.stderr)
        return 1
    try:
        cfg.passkey.hash = hash_passkey(new)
    except PasskeyError as e:
        print(f"Rejected: {e}", file=sys.stderr)
        return 1
    path = cfgmod.save_config(cfg)
    log.info("passkey changed")
    print(f"Passkey saved (argon2id hash) to {path}")
    return 0


# ---------------------------------------------------------------------- diag

def cmd_diag(cfg: cfgmod.Config) -> int:
    from .platform import get_adapter

    journal = Journal(cfgmod.journal_path())
    adapter = get_adapter(lambda e: None, journal, cfg, dev=True, diag=lambda line: print(line, flush=True))
    print("LapGuard diagnostics - nothing is armed or changed. Try: close/open the lid, unplug/plug AC,")
    print("plug a USB stick, plug headphones, connect Bluetooth audio, press Win+L. Ctrl+C to quit.\n")
    adapter.start()
    time.sleep(0.5)  # initial lid / power notifications arrive right after registration
    print("current state:", ", ".join(f"{k}={v}" for k, v in adapter.status().items()), "\n", flush=True)
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        adapter.stop()
    return 0


# ---------------------------------------------------------------------- console session

def cmd_console_session(cfg: cfgmod.Config, *, dev: bool) -> int:
    from .core import events as ev
    from .core.states import State
    from .engine import Engine

    if not cfg.has_passkey:
        print("No passkey set. Run:  lapguard --set-passkey", file=sys.stderr)
        return 2
    engine = Engine(cfg, dev=dev)
    done = threading.Event()

    def show(e: ev.Event) -> None:
        if isinstance(e, ev.StateChanged):
            print(f"\n>>> {e.old.upper()} -> {e.new.upper()}  ({e.reason})", flush=True)
            if e.new == State.ARMED.value:
                print("    keyboard is now CAPTURED. To disarm: any key, passkey, Enter.", flush=True)
            if e.new == State.DISARMED.value:
                done.set()
        elif isinstance(e, ev.PasskeyResult):
            print(f"\n>>> passkey {'OK' if e.ok else 'WRONG'} (failed attempts: {e.failed_attempts})", flush=True)
        elif isinstance(e, ev.PasskeyBufferChanged):
            print(f"\r    passkey: {'*' * e.length:<40}", end="", flush=True)
        elif isinstance(e, ev.Notice):
            print(f"\n[{e.level.upper()}] {e.message}", flush=True)
        elif isinstance(e, ev.SensorEvent) and not isinstance(e, ev.KeyPressed):
            print(f"\n[sensor] {e.describe()}", flush=True)

    engine.bus.subscribe(ev.Event, show)
    print("DEV console session. Safety exits, in order:")
    print("  1. any key, then your passkey, then Enter")
    print(f"  2. create the file {cfgmod.emergency_file()}")
    print("  3. wait: dev mode auto-disarms 60 s after arming (the keyboard hook also self-releases)")
    print("  4. Ctrl+Alt+Del -> Task Manager -> end python.exe   (then: lapguard --restore)")
    try:
        engine.start()
        engine.arm("cli")
        while not done.wait(0.2):
            pass
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        engine.stop()
    report = engine.last_restore
    print("\nSystem settings:", "restored" if report is None or report.ok else f"NOT fully restored: {report}")
    return 0


# ---------------------------------------------------------------------- simulate

_SIM_HELP = """\
Commands (anything not starting with ':' is typed as keys followed by Enter):
  :arm            start arming              :cancel         cancel the countdown
  :key            press one (wake) key      :sensor NAME    fire a sensor (keyboard, mouse, power,
  :status         show state / timers                       shutdown, lid, camera, devices, session)
  :force          force disarm              :quit           exit
While ARMED you can type the passkey straight away, or wake the prompt first
(:key, or any key) and then type it - both work.
"""


def cmd_simulate(cfg: cfgmod.Config, *, dev: bool) -> int:
    from .actions.logger import EventLogger
    from .core import events as ev
    from .core.bus import EventBus
    from .core.machine import MachineSettings, StateMachine
    from .core.passkey import make_verifier

    if cfg.has_passkey:
        verifier = make_verifier(cfg.passkey.hash)
        print("Using your configured passkey.")
    else:
        verifier = lambda s: s == "1234"  # noqa: E731
        print("No passkey configured - simulation passkey is 1234.")

    sensor_events = {
        "keyboard": lambda: ev.KeyPressed(key=ev.Key.OTHER, detail="simulated"),
        "mouse": lambda: ev.MouseActivity(detail="simulated"),
        "power": lambda: ev.AcUnplugged(detail="simulated"),
        "shutdown": lambda: ev.ShutdownAttempt(detail="simulated"),
        "lid": lambda: ev.LidClosed(detail="simulated"),
        "camera": lambda: ev.CameraTriggered(detail="simulated"),
        "devices": lambda: ev.DeviceChanged(name="Simulated USB stick", detail="simulated"),
        "session": lambda: ev.SessionEvent(detail="simulated"),
    }

    bus = EventBus()
    machine = StateMachine(verifier, MachineSettings.from_config(cfg, dev=dev), publish=bus.publish)
    bus.subscribe(ev.Event, machine.handle)
    EventLogger(bus)

    def show(e: ev.Event) -> None:
        if isinstance(e, ev.StateChanged):
            print(f"\n>>> {e.old.upper()} -> {e.new.upper()}  ({e.reason})")
        elif isinstance(e, ev.PasskeyResult):
            print(f"\n>>> passkey {'OK' if e.ok else 'WRONG'} (failed attempts: {e.failed_attempts})")

    bus.subscribe(ev.StateChanged, show)
    bus.subscribe(ev.PasskeyResult, show)
    bus.start()

    lines: queue.SimpleQueue[str | None] = queue.SimpleQueue()

    try:  # PowerShell pipes may prefix a BOM; utf-8-sig drops it
        sys.stdin.reconfigure(encoding="utf-8-sig")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    def reader() -> None:
        for line in sys.stdin:
            lines.put(line.rstrip("\r\n"))
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    print(_SIM_HELP + ("DEV mode: auto-disarm 60 s after arming.\n" if dev else ""))
    try:
        while True:
            machine.tick()
            try:
                line = lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None or line == ":quit":
                break
            if line == ":arm":
                bus.publish(ev.ArmRequested(source="simulate"))
            elif line == ":cancel":
                bus.publish(ev.CancelArming(source="simulate"))
            elif line == ":force":
                bus.publish(ev.ForceDisarm(reason="simulate :force"))
            elif line == ":key":
                bus.publish(ev.KeyPressed(key=ev.Key.OTHER))
            elif line.startswith(":sensor"):
                name = line.partition(" ")[2].strip()
                if name in sensor_events:
                    bus.publish(sensor_events[name]())
                else:
                    print(f"unknown sensor {name!r}; one of {', '.join(sensor_events)}")
            elif line == ":status":
                rem, dev_rem = machine.seconds_remaining(), machine.dev_seconds_remaining()
                print(f"state={machine.state.value} failed_attempts={machine.failed_attempts}"
                      + (f" timer={rem:.1f}s" if rem is not None else "")
                      + (f" dev_auto_disarm_in={dev_rem:.1f}s" if dev_rem is not None else ""))
            elif line.startswith(":"):
                print(_SIM_HELP)
            else:
                for ch in line:
                    bus.publish(ev.KeyPressed(key=ev.Key.CHAR, char=ch))
                bus.publish(ev.KeyPressed(key=ev.Key.ENTER))
    except KeyboardInterrupt:
        pass
    finally:
        time.sleep(0.2)  # let the last events print
        bus.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
