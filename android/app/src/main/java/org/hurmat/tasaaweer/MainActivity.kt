package org.hurmat.tasaaweer

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/** Just points the user at the accessibility settings; everything else is in ProbeService. */
class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (24 * resources.displayMetrics.density).toInt()
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad * 2, pad, pad)
            addView(TextView(this@MainActivity).apply {
                text = "Probe build. Turn on \"Hurmat Tasaaweer (probe)\" under Accessibility " +
                    "(if it's greyed out: App info → ⋮ → Allow restricted settings), then run the " +
                    "tests from the computer with adb."
                textSize = 16f
            })
            addView(Button(this@MainActivity).apply {
                text = "Open accessibility settings"
                setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
            })
        })
    }
}
