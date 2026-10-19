/*
 * Shelly Plug M Gen3 — Automatic Light Control
 *
 * Evening: Sunset until 21:30 -> ON, then OFF
 * Morning: 06:00 until sunrise -> ON
 * Otherwise: OFF
 *
 * Solar times: Shelly Schedule.Eval API
 * Location and time zone: Shelly device settings
 */

const CHECK_MS = 30000;

const MORNING_START = 360; // 06:00
const EVENING_END = 1290; // 21:30

let manualOverride = false;
let manualOverrideStart = 0;
let overrideUntil = 0;

let expectedState = null;
let ignoreEventsUntil = 0;
const running = true;

function log(msg) {
    print("[LightControl] " + msg);
}

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

function failSafe(reason) {
    log("Fail-safe: " + reason);

    if (!manualOverride) {
        setOutput(false, reason);
    }
}

function getTodayEvent(result, nowTs) {
    if (!result) {
        return null;
    }

    const nowDate = new Date(nowTs * 1000);
    const todayYear = nowDate.getFullYear();
    const todayMonth = nowDate.getMonth();
    const todayDay = nowDate.getDate();

    function isToday(ts) {
        if (typeof ts !== "number" || ts <= 0) {
            return false;
        }

        const d = new Date(ts * 1000);

        return (
            d.getFullYear() === todayYear &&
            d.getMonth() === todayMonth &&
            d.getDate() === todayDay
        );
    }

    if (isToday(result.prev)) {
        return result.prev;
    }

    if (isToday(result.next)) {
        return result.next;
    }

    return null;
}

function nextFixedBoundary(hour, minute, afterTs) {
    const d = new Date(afterTs * 1000);

    const year = d.getFullYear();
    const month = d.getMonth();
    const day = d.getDate();

    // Calculate today's local midnight from the current timestamp.
    const midnight =
        afterTs - (d.getHours() * 3600 + d.getMinutes() * 60 + d.getSeconds());

    let candidate = midnight + hour * 3600 + minute * 60;

    // Adjust for timezone offset changes.
    const targetDate = new Date(candidate * 1000);
    const offsetNow = d.getTimezoneOffset();
    const offsetTarget = targetDate.getTimezoneOffset();

    candidate += (offsetNow - offsetTarget) * 60;

    if (candidate <= afterTs) {
        const nextDay = new Date((midnight + 86400) * 1000);
        let nextMidnight = midnight + 86400;

        const dayOffset = nextDay.getTimezoneOffset();
        nextMidnight += (offsetNow - dayOffset) * 60;

        candidate = nextMidnight + hour * 3600 + minute * 60;

        const targetNext = new Date(candidate * 1000);
        candidate += (dayOffset - targetNext.getTimezoneOffset()) * 60;
    }

    return candidate;
}

function nextBoundary(afterTs, sunriseTs, sunsetTs) {
    const candidates = [];

    candidates.push(nextFixedBoundary(6, 0, afterTs));
    candidates.push(nextFixedBoundary(21, 30, afterTs));

    if (sunriseTs && sunriseTs > afterTs) {
        candidates.push(sunriseTs);
    }

    if (sunsetTs && sunsetTs > afterTs) {
        candidates.push(sunsetTs);
    }

    let next = candidates[0];

    for (let i = 1; i < candidates.length; i++) {
        if (candidates[i] < next) {
            next = candidates[i];
        }
    }

    return next;
}

function evaluate() {
    if (!running) {
        return;
    }

    Shelly.call("Sys.GetStatus", {}, function (sys, errCode, errMsg) {
        if (errCode !== 0 || !sys || !sys.unixtime) {
            failSafe("Cannot get system time");
            return;
        }

        const nowTs = sys.unixtime;
        const nowDate = new Date(nowTs * 1000);

        const currentMinutes = nowDate.getHours() * 60 + nowDate.getMinutes();

        Shelly.call(
            "Schedule.Eval",
            { timespec: "@sunrise", now: nowTs },
            function (sunriseResult, sunriseErr) {
                if (sunriseErr !== 0 || !sunriseResult) {
                    failSafe("Cannot calculate sunrise");
                    return;
                }

                Shelly.call(
                    "Schedule.Eval",
                    { timespec: "@sunset", now: nowTs },
                    function (sunsetResult, sunsetErr) {
                        if (sunsetErr !== 0 || !sunsetResult) {
                            failSafe("Cannot calculate sunset");
                            return;
                        }

                        const sunriseTs = getTodayEvent(sunriseResult, nowTs);
                        const sunsetTs = getTodayEvent(sunsetResult, nowTs);

                        if (!sunriseTs || !sunsetTs) {
                            failSafe("Sunrise or sunset unavailable for today");
                            return;
                        }

                        // Release a manual override at the next phase boundary.
                        if (manualOverride) {
                            if (overrideUntil === 0) {
                                overrideUntil = nextBoundary(
                                    manualOverrideStart,
                                    sunriseTs,
                                    sunsetTs,
                                );
                            }

                            if (nowTs >= overrideUntil) {
                                manualOverride = false;
                                overrideUntil = 0;
                                log("Manual override expired");
                            } else {
                                return;
                            }
                        }

                        const sunriseMinutes =
                            new Date(sunriseTs * 1000).getHours() * 60 +
                            new Date(sunriseTs * 1000).getMinutes();

                        const sunsetMinutes =
                            new Date(sunsetTs * 1000).getHours() * 60 +
                            new Date(sunsetTs * 1000).getMinutes();

                        let shouldBeOn = false;

                        // Evening: after sunset, but before 21:30.
                        if (
                            currentMinutes >= sunsetMinutes &&
                            currentMinutes < EVENING_END
                        ) {
                            shouldBeOn = true;
                        }

                        // Morning: from 06:00 until sunrise.
                        if (
                            currentMinutes >= MORNING_START &&
                            currentMinutes < sunriseMinutes
                        ) {
                            shouldBeOn = true;
                        }

                        setOutput(shouldBeOn, "Automatic schedule");
                    },
                );
            },
        );
    });
}

Shelly.addEventHandler(function (event) {
    if (!event || event.component !== "switch:0" || !event.info) {
        return;
    }

    if (typeof event.info.state !== "boolean") {
        return;
    }

    const state = event.info.state;

    // Ignore events caused by this script's own recent command.
    if (expectedState === state && Date.now() <= ignoreEventsUntil) {
        expectedState = null;
        return;
    }

    manualOverride = true;
    manualOverrideStart = Math.floor(Date.now() / 1000);
    overrideUntil = 0;

    log("Manual override activated: " + (state ? "ON" : "OFF"));
});

Timer.set(CHECK_MS, true, evaluate);

evaluate();
