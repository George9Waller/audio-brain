package com.georgewaller.audiobrain

import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** "just now", "4 min ago", "in 3 h", "Fri 05:00": short enough for a DAP screen. */
fun relative(instant: Instant?, now: Instant = Instant.now()): String {
    if (instant == null) return "never"
    val d = Duration.between(now, instant)
    val future = !d.isNegative
    val s = d.abs().seconds
    val amount = when {
        s < 45 -> return if (future) "now" else "just now"
        s < 3600 -> "${(s + 30) / 60} min"
        s < 86_400 -> "${(s + 1800) / 3600} h"
        s < 7 * 86_400 -> return DAY.format(instant.atZone(ZoneId.systemDefault()))
        else -> return DATE.format(instant.atZone(ZoneId.systemDefault()))
    }
    return if (future) "in $amount" else "$amount ago"
}

fun duration(seconds: Long): String = when {
    seconds < 60 -> "${seconds}s"
    seconds < 3600 -> "${seconds / 60}m ${seconds % 60}s"
    else -> "${seconds / 3600}h ${(seconds % 3600) / 60}m"
}

private val DAY = DateTimeFormatter.ofPattern("EEE HH:mm", Locale.UK)
private val DATE = DateTimeFormatter.ofPattern("d MMM", Locale.UK)
val CLOCK: DateTimeFormatter = DateTimeFormatter.ofPattern("EEE d MMM · HH:mm", Locale.UK)
