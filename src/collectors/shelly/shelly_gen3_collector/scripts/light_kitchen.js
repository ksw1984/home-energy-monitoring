/*
 * Shelly Plug M Gen3 — Automatic Light Control
 *
 * Schedule:
 *   - Turn ON at sunset, but only if sunset is before 21:30.
 *   - Turn OFF at 21:30, regardless of sunset.
 *   - Turn ON at 06:00 only if sunrise is still in the future.
 *   - Turn OFF at sunrise.
 *
 * Manual override:
 *   - Manual switching is respected until the next schedule boundary.
 *   - The next boundary is the earliest upcoming 06:00, 21:30,
 *     sunrise or sunset event.
 *   - Restarting the script clears the override and immediately
 *     applies the schedule for the current time.
 *
 * Implementation:
 *   - Uses Shelly Schedule.Eval for fixed and solar event timestamps.
 *   - Compares Unix timestamps directly; no custom date arithmetic.
 *   - Checks the schedule every 30 seconds.
 *
 * Note:
 *   - Device location and timezone must be configured in Shelly.
 */

const CHECK_MS = 30000;

const EVENTS = [
    { name: "06:00", spec: "0 0 6 * * *", on: true },
    { name: "21:30", spec: "0 30 21 * * *", on: false },
    { name: "sunrise", spec: "@sunrise", on: false },
    { name: "sunset", spec: "@sunset", on: true },
];

let manualOverride = false;
let overrideUntil = 0;

let expectedState = null;
let ignoreEventsUntil = 0;

function log(msg) {
    print("[LightControl] " + msg);
}

/*
 * Switch the output only when its current state differs.
 * A short-lived expectedState marker helps distinguish commands
 * issued by this script from manual switch changes.
 */
function setOutput(on, reason) {
    const status = Shelly.getComponentStatus("switch", 0);

    if (!status) {
        log("Cannot read switch status: " + reason);
        return;
    }

    if (status.output === on) {
        return;
    }

    expectedState = on;
    ignoreEventsUntil = Date.now() + 2000;

    Shelly.call(
        "Switch.Set",
        { id: 0, on: on },
        function (res, errCode, errMsg) {
            if (errCode !== 0) {
                expectedState = null;
                log("Switch.Set failed: " + errMsg);
            } else {
                log("Switched " + (on ? "ON" : "OFF") + ": " + reason);
            }
        },
    );
}

/*
 * Read the previous and next occurrence of each event using
 * Shelly's Schedule.Eval API.
 *
 * Calls are made sequentially to avoid overlapping requests.
 * callback receives (results, null) on success or (null, error).
 */
function getEvents(nowTs, callback) {
    const results = [];
    let index = 0;

    function next() {
        if (index >= EVENTS.length) {
            callback(results, null);
            return;
        }

        const event = EVENTS[index];

        Shelly.call(
            "Schedule.Eval",
            { timespec: event.spec, now: nowTs },
            function (result, errCode, errMsg) {
                if (errCode !== 0 || !result) {
                    callback(
                        null,
                        "Schedule.Eval failed for " +
                            event.name +
                            ": " +
                            errMsg,
                    );
                    return;
                }

                results.push({
                    name: event.name,
                    spec: event.spec,
                    prev: result.prev || 0,
                    next: result.next || 0,
                });

                index++;
                next();
            },
        );
    }

    next();
}

/*
 * Find the next schedule boundary after the supplied timestamp.
 * All times are returned by Shelly as Unix timestamps, so no
 * local date calculations or timezone-offset calculations are needed.
 */
function findNextBoundary(events, afterTs) {
    let nextBoundary = 0;

    for (let i = 0; i < events.length; i++) {
        const t = events[i].next;

        if (t > afterTs && (!nextBoundary || t < nextBoundary)) {
            nextBoundary = t;
        }
    }

    return nextBoundary;
}

/*
 * Find the most recent occurrence of a named event.
 * The result is a timestamp, or 0 if no previous occurrence exists.
 */
function findPreviousEvent(events, name) {
    for (let i = 0; i < events.length; i++) {
        if (events[i].name === name) {
            return events[i].prev;
        }
    }

    return 0;
}

/*
 * Determine the desired output from the actual event timestamps.
 *
 * Morning ON window:
 *   06:00 <= now < sunrise
 *
 * Evening ON window:
 *   sunset <= now < 21:30
 *
 * Comparing timestamps directly also handles the cases where
 * sunrise is before 06:00 or sunset is after 21:30.
 */
function calculateDesiredState(events, nowTs) {
    const morningStart = findPreviousEvent(events, "06:00");
    const eveningEnd = findPreviousEvent(events, "21:30");
    const sunrise = findPreviousEvent(events, "sunrise");
    const sunset = findPreviousEvent(events, "sunset");

    if (!morningStart || !eveningEnd || !sunrise || !sunset) {
        return null;
    }

    const morningOn = nowTs >= morningStart && nowTs < sunrise;

    const eveningOn = nowTs >= sunset && nowTs < eveningEnd;

    return morningOn || eveningOn;
}

/*
 * Fail-safe behavior:
 * if time or schedule information cannot be retrieved, log the
 * problem and turn OFF unless a manual override is active.
 */
function failSafe(reason) {
    log("Fail-safe: " + reason);

    if (!manualOverride) {
        setOutput(false, reason);
    }
}

/*
 * Evaluate the schedule.
 *
 * A restart starts with manualOverride=false, so the current
 * schedule is applied immediately.
 */
function evaluate() {
    Shelly.call("Sys.GetStatus", {}, function (sys, errCode, errMsg) {
        if (errCode !== 0 || !sys || !sys.unixtime) {
            failSafe("Cannot get system time: " + errMsg);
            return;
        }

        const nowTs = sys.unixtime;

        getEvents(nowTs, function (events, error) {
            if (error) {
                failSafe(error);
                return;
            }

            /*
             * While manually overridden, leave the output alone
             * until the next schedule boundary.
             */
            if (manualOverride) {
                if (overrideUntil && nowTs < overrideUntil) {
                    return;
                }

                manualOverride = false;
                overrideUntil = 0;
                log("Manual override expired");
            }

            const desiredState = calculateDesiredState(events, nowTs);

            if (desiredState === null) {
                failSafe("Required schedule event unavailable");
                return;
            }

            setOutput(desiredState, "Automatic schedule");
        });
    });
}

/*
 * Detect manual changes to switch:0.
 * Script-generated switch commands are ignored when they match
 * the expected state and occur within the short ignore window.
 *
 * For a genuine manual change, calculate the next boundary using
 * the same Shelly API events used by the scheduler.
 */
Shelly.addEventHandler(function (event) {
    if (
        !event ||
        event.component !== "switch:0" ||
        !event.info ||
        typeof event.info.state !== "boolean"
    ) {
        return;
    }

    const state = event.info.state;

    // Ignore a recent event caused by our own command.
    if (expectedState === state && Date.now() <= ignoreEventsUntil) {
        expectedState = null;
        return;
    }

    manualOverride = true;
    overrideUntil = 0;

    log("Manual override activated: " + (state ? "ON" : "OFF"));

    Shelly.call("Sys.GetStatus", {}, function (sys, errCode, errMsg) {
        if (errCode !== 0 || !sys || !sys.unixtime) {
            log("Cannot calculate manual override boundary");
            return;
        }

        const nowTs = sys.unixtime;

        getEvents(nowTs, function (events, error) {
            if (error) {
                log("Cannot calculate override boundary: " + error);
                return;
            }

            overrideUntil = findNextBoundary(events, nowTs);

            if (!overrideUntil) {
                log("No upcoming schedule boundary found");
            }
        });
    });
});

/*
 * Start periodic evaluation and immediately apply the current
 * schedule. Timer.set uses the Shelly scripting API signature:
 * Timer.set(milliseconds, repeat, callback).
 */
Timer.set(CHECK_MS, true, evaluate);

evaluate();
