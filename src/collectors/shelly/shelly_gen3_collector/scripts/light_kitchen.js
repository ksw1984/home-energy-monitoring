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

let running = false;
let lastDecision = null;

function minutesOfDay(date) {
    return date.getHours() * 60 + date.getMinutes() + date.getSeconds() / 60;
}

function sameLocalDay(timestamp, date) {
    const d = new Date(timestamp * 1000);
    return (
        d.getFullYear() === date.getFullYear() &&
        d.getMonth() === date.getMonth() &&
        d.getDate() === date.getDate()
    );
}

function todayEvent(result, date) {
    if (!result) return null;

    if (typeof result.prev === "number" && sameLocalDay(result.prev, date)) {
        return result.prev;
    }

    if (typeof result.next === "number" && sameLocalDay(result.next, date)) {
        return result.next;
    }

    return null;
}

function formatTime(timestamp) {
    const d = new Date(timestamp * 1000);
    return (
        ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2)
    );
}

function setOutput(on, reason) {
    const status = Shelly.getComponentStatus("switch", 0);

    if (!status) {
        console.log("ERROR: Switch status unavailable");
        return;
    }

    if (status.output === on) return;

    Shelly.call(
        "Switch.Set",
        { id: 0, on: on },
        function (result, err, message) {
            if (err) {
                console.log("ERROR Switch.Set:", err, message);
                return;
            }

            console.log("Output:", on ? "ON" : "OFF", "|", reason);
        },
    );
}

function failSafe(reason) {
    running = false;
    console.log("ERROR:", reason, "- switching OFF");
    setOutput(false, reason);
}

function evaluate() {
    if (running) return;
    running = true;

    const sys = Shelly.getComponentStatus("sys");

    if (!sys || typeof sys.unixtime !== "number" || sys.unixtime < 1700000000) {
        failSafe("System time invalid");
        return;
    }

    const nowTimestamp = sys.unixtime;
    const now = new Date(nowTimestamp * 1000);
    const currentMinutes = minutesOfDay(now);

    // Get sunrise using Shelly's solar-time calculation.
    Shelly.call(
        "Schedule.Eval",
        {
            timespec: "@sunrise",
            now: nowTimestamp,
        },
        function (rise, riseErr, riseMessage) {
            if (riseErr || !rise) {
                failSafe(
                    "Schedule.Eval @sunrise: " + riseErr + " " + riseMessage,
                );
                return;
            }

            // Get sunset using the same API.
            Shelly.call(
                "Schedule.Eval",
                {
                    timespec: "@sunset",
                    now: nowTimestamp,
                },
                function (sun, sunErr, sunMessage) {
                    if (sunErr || !sun) {
                        failSafe(
                            "Schedule.Eval @sunset: " +
                                sunErr +
                                " " +
                                sunMessage,
                        );
                        return;
                    }

                    const sunriseTimestamp = todayEvent(rise, now);
                    const sunsetTimestamp = todayEvent(sun, now);

                    if (sunriseTimestamp === null || sunsetTimestamp === null) {
                        failSafe("Solar event for today is missing");
                        return;
                    }

                    const sunrise = new Date(sunriseTimestamp * 1000);
                    const sunset = new Date(sunsetTimestamp * 1000);

                    const sunriseMinutes = minutesOfDay(sunrise);
                    const sunsetMinutes = minutesOfDay(sunset);

                    // Condition 1:
                    // Turn ON after sunset, but only before 21:30.
                    const eveningOn =
                        sunsetMinutes < EVENING_END &&
                        currentMinutes >= sunsetMinutes &&
                        currentMinutes < EVENING_END;

                    // Condition 2:
                    // At and after 21:30, the evening window is closed.

                    // Condition 3:
                    // At 06:00, turn ON only if sunrise is still ahead.
                    const morningOn =
                        sunriseMinutes > MORNING_START &&
                        currentMinutes >= MORNING_START &&
                        currentMinutes < sunriseMinutes;

                    // Condition 4:
                    // At sunrise, the morning window ends automatically.
                    const desired = morningOn || eveningOn;

                    const reason = morningOn
                        ? "Morning window"
                        : eveningOn
                          ? "Evening window"
                          : "Outside active windows";

                    const decisionKey = desired ? "ON" : "OFF";

                    if (lastDecision !== decisionKey) {
                        console.log(
                            "Time:",
                            sys.time,
                            "| Sunrise:",
                            formatTime(sunriseTimestamp),
                            "| Sunset:",
                            formatTime(sunsetTimestamp),
                            "| Target:",
                            decisionKey,
                        );
                        lastDecision = decisionKey;
                    }

                    running = false;
                    setOutput(desired, reason);
                },
            );
        },
    );
}

console.log("Light control started using Shelly solar times");

evaluate();
Timer.set(CHECK_MS, true, evaluate);
