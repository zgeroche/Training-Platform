package com.trainingplatform.trainingsync

import com.samsung.android.sdk.health.data.HealthDataStore
import com.samsung.android.sdk.health.data.data.entries.ExerciseSession
import com.samsung.android.sdk.health.data.request.DataType
import com.samsung.android.sdk.health.data.request.DataTypes
import com.samsung.android.sdk.health.data.request.LocalTimeFilter
import com.samsung.android.sdk.health.data.request.Ordering
import java.time.Duration
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneOffset

/**
 * What one run looks like through the Samsung Health Data SDK, plus a few checks that tell us
 * what the server will have to work out itself. (Read-only: nothing is sent anywhere yet.)
 *
 * Samsung heart rate is deliberately never read out of the log.
 */
data class RunSummary(
    val uid: String,
    val start: Instant,
    val end: Instant,
    val zoneOffset: ZoneOffset?,
    val moving: Duration,
    val distanceM: Float?,
    val routePoints: Int,
    val logEntries: Int,
    val speedReadings: Int,
    val cadenceReadings: Int,
    /** Typical seconds between speed readings. */
    val medianGapSec: Double,
    /** Seconds spent in gaps over 5 s between speed readings: candidate pauses. */
    val longGapsSec: Double,
    /** Distance from adding up speed x time between readings (the log has no distance field). */
    val distanceFromSpeedM: Double,
) {
    val wallClock: Duration get() = Duration.between(start, end)
    val pausedByClock: Duration get() = wallClock.minus(moving)
}

class RunReader(private val store: HealthDataStore) {

    suspend fun recentRuns(days: Long): List<RunSummary> {
        val request = DataTypes.EXERCISE.readDataRequestBuilder
            .setLocalTimeFilter(LocalTimeFilter.since(LocalDateTime.now().minusDays(days)))
            .setOrdering(Ordering.DESC)
            .build()
        val points = store.readData(request).dataList
        return points.flatMap { point ->
            val type = point.getValue(DataType.ExerciseType.EXERCISE_TYPE)
            if (type !in RUN_TYPES) return@flatMap emptyList()
            val sessions = point.getValue(DataType.ExerciseType.SESSIONS) ?: emptyList()
            sessions.mapIndexed { i, s ->
                // A run is one data point; it may hold more than one session (e.g. restarts).
                summarize(if (sessions.size == 1) point.uid else "${point.uid}#$i", point.zoneOffset, s)
            }
        }
    }

    private fun summarize(uid: String, zone: ZoneOffset?, s: ExerciseSession): RunSummary {
        val log = s.log.orEmpty().sortedBy { it.timestamp }
        // The log interleaves speed/cadence readings with heart-rate-only ones about 0.5 s apart,
        // and heart rate keeps logging while paused. So measure on the speed readings alone.
        val speedLog = log.filter { it.speed != null }
        val gaps = speedLog.zipWithNext { a, b -> Duration.between(a.timestamp, b.timestamp).toMillis() / 1000.0 }
        var fromSpeed = 0.0
        speedLog.zipWithNext { a, b ->
            val dt = Duration.between(a.timestamp, b.timestamp).toMillis() / 1000.0
            if (dt in 0.0..5.0) fromSpeed += a.speed!! * dt  // skip long gaps: likely paused
        }
        return RunSummary(
            uid = uid,
            start = s.startTime,
            end = s.endTime,
            zoneOffset = zone,
            moving = s.duration,
            distanceM = s.distance,
            routePoints = s.route.orEmpty().size,
            logEntries = log.size,
            speedReadings = log.count { it.speed != null },
            cadenceReadings = log.count { it.cadence != null },
            medianGapSec = gaps.sorted().let { if (it.isEmpty()) 0.0 else it[it.size / 2] },
            longGapsSec = gaps.filter { it > 5.0 }.sum(),
            distanceFromSpeedM = fromSpeed,
        )
    }

    companion object {
        val RUN_TYPES = setOf(
            DataType.ExerciseType.PredefinedExerciseType.RUNNING,
            DataType.ExerciseType.PredefinedExerciseType.TRACK_RUNNING,
        )
    }
}
