let running = false;
let finishTimer = null;
let checkTimer = null;

// Timing
const slowInterval_ms = 10 * 60 * 1000; // 10 minutes
const fastInterval_ms = 10 * 1000; // 10 seconds
const finishDelay_ms = 5 * 60 * 1000; // 5 minutes

// Power thresholds
const runningPower_W = 80; // Detect charging
const finishedPower_W = 1; // Detect charging finished

// Notification
const ntfyUrl = "http://192.168.178.11:8090/tenways-7f92";

function switchToSlowChecks() {
    Timer.clear(checkTimer);
    checkTimer = Timer.set(slowInterval_ms, true, checkPower);

    print("Switched to", slowInterval_ms / 60000, "minute checks");
}

function switchToFastChecks() {
    Timer.clear(checkTimer);
    checkTimer = Timer.set(fastInterval_ms, true, checkPower);

    print("Switched to", fastInterval_ms / 1000, "second checks");
}

function sendNotification(message) {
    Shelly.call(
        "HTTP.POST",
        {
            url: ntfyUrl,
            body: message,
            content_type: "text/plain",
            headers: {
                Title: "Tenways-Laden",
            },
        },
        function (_result, error_code, error_message) {
            if (error_code !== 0) {
                print("ntfy error:", error_code, error_message);
            }
        },
    );
}

function finishCharging() {
    print("Charging finished - switching Shelly output OFF");

    Shelly.call(
        "Switch.Set",
        {
            id: 0,
            on: false,
        },
        function (_result, error_code, error_message) {
            if (error_code !== 0) {
                print("Switch OFF error:", error_code, error_message);

                sendNotification(
                    "Tenways fertig geladen, aber die Steckdose konnte nicht ausgeschaltet werden!",
                );

                return;
            }

            print("Shelly output switched OFF");

            sendNotification(
                "Tenways fertig geladen. Steckdose wurde automatisch ausgeschaltet.",
            );
        },
    );

    running = false;

    switchToSlowChecks();
}

function startFinishTimer() {
    if (finishTimer !== null) {
        return;
    }

    print(
        "Below",
        finishedPower_W,
        "W - starting",
        finishDelay_ms / 60000,
        "minute timer",
    );

    finishTimer = Timer.set(finishDelay_ms, false, function () {
        finishTimer = null;
        finishCharging();
    });
}

function cancelFinishTimer() {
    if (finishTimer === null) {
        return;
    }

    Timer.clear(finishTimer);
    finishTimer = null;

    print("Finish timer cancelled");
}

function checkPower() {
    Shelly.call(
        "PM1.GetStatus",
        { id: 0 },
        function (result, error_code, error_message) {
            if (error_code !== 0) {
                print("PM1 error:", error_code, error_message);
                return;
            }

            const power_W = result.apower;

            print("Tenways charger:", power_W, "W");

            // IDLE -> CHARGING
            if (!running) {
                if (power_W > runningPower_W) {
                    running = true;

                    print("Charging started:", power_W, "W");

                    sendNotification(
                        "Tenways wird geladen. Leistung: " + power_W + " W.",
                    );

                    switchToFastChecks();
                }

                return;
            }

            // CHARGING
            if (power_W >= finishedPower_W) {
                cancelFinishTimer();
                return;
            }

            // Below 1 W: start finish timer
            if (power_W < finishedPower_W) {
                startFinishTimer();
            }
        },
    );
}

// Start with slow checks
checkTimer = Timer.set(slowInterval_ms, true, checkPower);

print(
    "Tenways charging detection started - initial interval:",
    slowInterval_ms / 60000,
    "minutes",
);
