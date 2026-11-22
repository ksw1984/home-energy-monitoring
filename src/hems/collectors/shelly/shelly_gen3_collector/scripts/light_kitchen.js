/*
 * Shelly Plug M Gen3 — Automatic Light Control
 *
 * Schedule:
 *   - Turn ON at sunset minus 20 minutes.
 *   - Turn OFF at sunrise plus 20 minutes.
 *   - The fixed 06:00 and 21:30 events remain active; the most recent
 *     event determines the output state.
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

// Vars
let CHECK_MS = 30000;

let EVENTS = [
    { name: "06:00", spec: "0 0 6 * * *", on: true },
    { name: "21:30", spec: "0 30 21 * * *", on: false },
    { name: "sunrise+20m", spec: "@sunrise+20m", on: false },
    { name: "sunset-20m", spec: "@sunset-20m", on: true },
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
    let status = Shelly.getComponentStatus("switch", 0);

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
    let results = [];
    let index = 0;

    function next() {
        if (index >= EVENTS.length) {
            callback(results, null);
            return;
        }

        let event = EVENTS[index];

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
 * All times are returned by Shelly as Unix timestamps.
 */
function findNextBoundary(events, afterTs) {
    let nextBoundary = 0;

    for (let i = 0; i < events.length; i++) {
        let t = events[i].next;

        if (t > afterTs && (!nextBoundary || t < nextBoundary)) {
            nextBoundary = t;
        }
    }

    return nextBoundary;
}

/*
 * Evaluate the schedule using the most recent event. The same state
 * rule as the working plug script: whichever scheduled event occurred
 * most recently determines whether the output is ON or OFF.
 *
 * Manual switching is respected until the next upcoming boundary.
 */
function evaluate() {
    Shelly.call("Sys.GetStatus", {}, function (sys, errCode, errMsg) {
        if (errCode !== 0 || !sys || !sys.unixtime) {
            log("Cannot read device time: " + errMsg);
            return;
        }

        let nowTs = sys.unixtime;

        getEvents(nowTs, function (events, error) {
            if (error) {
                log(error);
                return;
            }

            if (manualOverride) {
                if (overrideUntil && nowTs < overrideUntil) {
                    return;
                }

                manualOverride = false;
                overrideUntil = 0;
                log("Manual override ended");
            }

            let latest = null;

            for (let i = 0; i < events.length; i++) {
                if (
                    events[i].prev > 0 &&
                    (!latest || events[i].prev > latest.prev)
                ) {
                    latest = events[i];
                }
            }

            if (!latest) {
                log("Cannot determine current phase");
                return;
            }

            setOutput(latest.on, "Latest event: " + latest.name);
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

    let state = event.info.state;

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

        let nowTs = sys.unixtime;

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
