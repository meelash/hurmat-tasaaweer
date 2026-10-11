#!/usr/bin/env bash
# Install the app on a USB-connected phone, run the probe tests, or switch
# between the probe service and the blurring service.
#
#   android/probe.sh install          build and install
#   android/probe.sh rate             screenshot speed (have an app open, e.g. Facebook)
#   android/probe.sh overlay          is our overlay left out of the window's screenshot?
#   android/probe.sh nodes            image descriptions the open app exposes
#   android/probe.sh mixed            is the screenshot limit shared between window and display shots?
#   android/probe.sh scroll           scroll events while the script swipes the feed
#   android/probe.sh pull             copy the probe's saved screenshots here
#   android/probe.sh blur             turn on the blurring service (turns the probe service off)
#   android/probe.sh probe            turn the probe service back on instead
#   android/probe.sh log              follow the blurring service's log
#   android/probe.sh set k=v ...      set options (model=yolo11n-seg, minSizeDp=64, tileAspect=1.3,
#                                     gpu=false, threads=4, mode=figure|face,
#                                     poseModel=yolo11s-pose|none)
#                                     and restart the service; "set" alone resets to defaults
set -euo pipefail
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SELF")"
export JAVA_HOME=${JAVA_HOME:-/usr/lib/jvm/temurin-21-jdk}
PKG=org.hurmat.tasaaweer

case "${1:-}" in
install)
    ./gradlew assembleDebug -q
    adb install -r app/build/outputs/apk/debug/app-debug.apk
    adb shell am start -n $PKG/.MainActivity >/dev/null
    echo "Installed. On the phone: enable 'Hurmat Tasaaweer (probe)' in Accessibility settings."
    echo "If it's greyed out: Settings → Apps → Hurmat Tasaaweer → ⋮ → Allow restricted settings."
    ;;
rate|overlay|nodes|mixed|scroll)
    wait=$([ "$1" = rate ] && echo 8 || echo 3)
    [ "$1" = overlay ] && wait=7
    adb logcat -c
    adb shell am broadcast -a $PKG.PROBE --es test "$1" >/dev/null
    if [ "$1" = scroll ]; then
        # Two flings up the screen, like a user scrolling the feed.
        sleep 0.5; adb shell input swipe 540 1900 540 900 250
        sleep 1.2; adb shell input swipe 540 1900 540 1100 400
    fi
    sleep "$wait"
    adb logcat -d -s Hurmat:I | grep -v '^-' | sed 's/^.*Hurmat *: //'
    ;;
blur|probe)
    svc=$([ "$1" = blur ] && echo HurmatService || echo ProbeService)
    # Retried: right after an install the setting can be reset.
    for _ in 1 2 3; do
        adb shell settings put secure enabled_accessibility_services "$PKG/$PKG.$svc"
        adb shell settings put secure accessibility_enabled 1
        sleep 2
        adb shell dumpsys accessibility | grep -q "Bound services:{Service\[label=Hurmat" && { echo "$svc on"; exit 0; }
    done
    echo "couldn't enable $svc; turn it on in Settings → Accessibility" >&2; exit 1
    ;;
set)
    shift
    printf '%s\n' "$@" > /tmp/hurmat-config.properties
    adb push /tmp/hurmat-config.properties "/sdcard/Android/data/$PKG/files/config.properties" >/dev/null
    "$SELF" probe >/dev/null; "$SELF" blur
    ;;
log)
    adb logcat -s Hurmat:I AndroidRuntime:E | sed -u 's/^[0-9-]* [0-9:.]* *[0-9]* *[0-9]* [A-Z] [A-Za-z]* *: //'
    ;;
pull)
    mkdir -p probe-results
    adb pull "/sdcard/Android/data/$PKG/files/." probe-results/
    ;;
*)
    sed -n '2,9p' "$0"; exit 1 ;;
esac
