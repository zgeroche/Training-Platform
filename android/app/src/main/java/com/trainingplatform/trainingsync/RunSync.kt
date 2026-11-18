package com.trainingplatform.trainingsync

import com.samsung.android.sdk.health.data.HealthDataStore
import java.time.ZoneId

/** What a sync did, in words for the screen or the log. */
data class SyncOutcome(val sent: Int, val message: String)

/**
 * Sends runs the PC doesn't have yet: anything that started after the server's newest run.
 * Used by both the Sync to PC button and the background check, so they behave the same.
 */
class RunSync(private val store: HealthDataStore, private val client: ServerClient = ServerClient()) {

    suspend fun syncNewRuns(lookbackDays: Long = 60, progress: (String) -> Unit = {}): SyncOutcome {
        if (!client.configured) {
            return SyncOutcome(0, "No server address or upload key in this build (android/training.properties).")
        }
        progress("Checking what the PC already has…")
        val latest = client.latestStart()
        val toSend = RunReader(store).recentSessions(lookbackDays)
            .filter { latest == null || it.session.startTime.toEpochMilli() > latest }
            .sortedBy { it.session.startTime }
        if (toSend.isEmpty()) {
            return SyncOutcome(0, "Up to date: the PC already has every run from the last $lookbackDays days.")
        }
        val lines = mutableListOf<String>()
        toSend.forEachIndexed { i, run ->
            val label = dateFormat.format(run.session.startTime.atZone(run.zoneOffset ?: ZoneId.systemDefault()))
            progress("Sending ${i + 1} of ${toSend.size}: $label…")
            lines += "$label: " + client.upload(run.toUpload())
        }
        val noun = if (toSend.size == 1) "run" else "runs"
        return SyncOutcome(toSend.size, "Sent ${toSend.size} $noun to the PC.\n" + lines.joinToString("\n"))
    }
}
