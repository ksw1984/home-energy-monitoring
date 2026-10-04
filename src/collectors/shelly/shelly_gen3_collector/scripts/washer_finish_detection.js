let running = false;
let finishTimer = null;
let checkTimer = null;

// Timing
const slowInterval_ms = 10 * 60 * 1000; // 10 minutes
const fastInterval_ms = 10 * 1000; // 10 seconds
const finishDelay_ms = 5 * 60 * 1000; // 5 minutes

// Power thresholds
const runningPower_W = 100; // Detect washer running
const finishedPower_W = 5; // Detect washer stopped

// Notification
const ntfyUrl = "http://192.168.178.11:8090/waschmaschine-7f92";

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

function sendFinishedNotification() {
    Shelly.call(
        "HTTP.POST",
        {
            url: ntfyUrl,
            body: "Washer finished",
            content_type: "text/plain",
            headers: {
                Title: "Waschmaschine fertig",
            },
        },
        function (_result, error_code, error_message) {
            print("ntfy:", error_code, error_message);
        },
    );
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

        sendFinishedNotification();

        running = false;

        print("Washer marked as finished");

        switchToSlowChecks();
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
                print("PM1 error:", error_message);
                return;
            }

            const power_W = result.apower;

            print("Washer:", power_W, "W");

            // ------------------------------------------------------------
            // IDLE -> RUNNING
            // ------------------------------------------------------------
            if (!running) {
                if (power_W > runningPower_W) {
                    running = true;

                    print("Washer started:", power_W, "W");

                    switchToFastChecks();
                }

                return;
            }

            // ------------------------------------------------------------
            // RUNNING
            // ------------------------------------------------------------

            // Washer is clearly running again.
            // Cancel a pending finish timer, but stay in RUNNING state.
            if (power_W >= finishedPower_W) {
                cancelFinishTimer();
                return;
            }

            // Washer is below the finish threshold.
            // Start the finish timer if none is running.
            if (power_W < finishedPower_W) {
                startFinishTimer();
            }
        },
    );
}

// Start with slow checks
checkTimer = Timer.set(slowInterval_ms, true, checkPower);

print(
    "Washer finish detection script started - initial interval:",
    slowInterval_ms / 60000,
    "minutes",
);
