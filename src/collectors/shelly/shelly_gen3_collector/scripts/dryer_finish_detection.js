let running = false;
let finishTimer = null;
let checkTimer = null;

// Timing configuration
let slowInterval_ms = 10 * 60 * 1000;  // 10 minutes
let fastInterval_ms = 10 * 1000;       // 10 seconds
let finishDelay_ms = 5 * 60 * 1000;     // 5 minutes

// Power thresholds
let runningPower_W = 100;  // Dryer is considered running above this power
let finishedPower_W = 5;   // Dryer is considered finished below this power

// Notification
let ntfyUrl = "http://192.168.178.11:8090/trockner-7f92";


function checkPower() {
  Shelly.call(
    "PM1.GetStatus",
    { id: 0 },
    function (result, error_code, error_message) {
      if (error_code !== 0) {
        print("PM1 error:", error_message);
        return;
      }

      let power_W = result.apower;
      print("Dryer:", power_W, "W");

      // Dryer is running
      if (power_W > runningPower_W) {
        if (!running) {
          print("Dryer started");

          // Switch to fast checks
          Timer.clear(checkTimer);
          checkTimer = Timer.set(
            fastInterval_ms,
            true,
            checkPower
          );

          print(
            "Switched to",
            fastInterval_ms / 1000,
            "second checks"
          );
        }

        running = true;

        // Cancel a pending finish timer
        if (finishTimer !== null) {
          Timer.clear(finishTimer);
          finishTimer = null;
          print("Finish timer cancelled");
        }

        return;
      }

      // Cancel the finish timer if power rises back to the finished threshold
      if (finishTimer !== null && power_W >= finishedPower_W) {
        Timer.clear(finishTimer);
        finishTimer = null;
        print("Finish timer cancelled");
      }

      // Dryer was running and power dropped below the finished threshold
      if (
        running &&
        power_W < finishedPower_W &&
        finishTimer === null
      ) {
        print(
          "Below",
          finishedPower_W,
          "W - starting",
          finishDelay_ms / 60000,
          "minute timer"
        );

        finishTimer = Timer.set(
          finishDelay_ms,
          false,
          function () {
            finishTimer = null;

            Shelly.call(
              "PM1.GetStatus",
              { id: 0 },
              function (result, error_code, error_message) {
                if (error_code !== 0) {
                  print("PM1 error:", error_message);
                  return;
                }

                let currentPower_W = result.apower;

                if (
                  currentPower_W < finishedPower_W &&
                  running
                ) {
                  Shelly.call(
                    "HTTP.POST",
					{
					  url: ntfyUrl,
					  body: "Dryer finished",
					  content_type: "text/plain",
					  headers: {
					    "Title": "Trockner fertig"
					}
					},
                    function (result, error_code, error_message) {
                      print(
                        "ntfy:",
                        error_code,
                        error_message
                      );
                    }
                  );

                  running = false;
                  print("Dryer marked as finished");

                  // Return to slow checks
                  Timer.clear(checkTimer);
                  checkTimer = Timer.set(
                    slowInterval_ms,
                    true,
                    checkPower
                  );

                  print(
                    "Switched to",
                    slowInterval_ms / 60000,
                    "minute checks"
                  );
                } else {
                  print(
                    "Dryer is still active:",
                    currentPower_W,
                    "W"
                  );
                }
              }
            );
          }
        );
      }
    }
  );
}


// Start with slow checks
checkTimer = Timer.set(
  slowInterval_ms,
  true,
  checkPower
);

print(
  "Dryer finish detection script started - initial interval:",
  slowInterval_ms / 60000,
  "minutes"
);
