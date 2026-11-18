package com.trainingplatform.trainingsync

import com.google.gson.FieldNamingPolicy
import com.google.gson.GsonBuilder
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.time.Duration

/*
 * Upload format for POST /api/samsung/runs (see app/samsung/ingest.py on the server).
 * Gson turns camelCase names into the server's snake_case (distanceM -> distance_m).
 * There is deliberately no heart-rate field anywhere: WHOOP is the heart-rate source.
 */
data class RoutePointUpload(val t: Long, val lat: Float, val lon: Float, val alt: Float?, val acc: Float?)
data class SampleUpload(val t: Long, val distanceM: Double, val speedMps: Float, val cadence: Float?)
data class PauseUpload(val start: Long, val end: Long)

data class RunUpload(
    val id: String,
    val exerciseType: Int,
    val start: Long,
    val end: Long,
    val utcOffsetMinutes: Int?,
    val durationMs: Long,
    val distanceM: Float?,
    val meanSpeedMps: Float?,
    val maxSpeedMps: Float?,
    val meanCadence: Float?,
    val maxCadence: Float?,
    val altitudeGainM: Float?,
    val altitudeLossM: Float?,
    val minAltitudeM: Float?,
    val maxAltitudeM: Float?,
    val vo2Max: Float?,
    val pauses: List<PauseUpload>,
    val route: List<RoutePointUpload>,
    val samples: List<SampleUpload>,
)

private const val RUNNING = 1002   // Samsung's exercise type number for running (the export uses it too)
private const val PAUSE_GAP_SEC = 5.0

fun RunSession.toUpload(): RunUpload {
    val s = session
    val speedLog = s.log.orEmpty().filter { it.speed != null }.sortedBy { it.timestamp }

    // Distance isn't in the log, so add up speed x time; a gap over 5 s is a pause, not distance.
    val samples = ArrayList<SampleUpload>(speedLog.size)
    val pauses = mutableListOf<PauseUpload>()
    var distance = 0.0
    speedLog.forEachIndexed { i, r ->
        if (i > 0) {
            val prev = speedLog[i - 1]
            val dt = Duration.between(prev.timestamp, r.timestamp).toMillis() / 1000.0
            if (dt > PAUSE_GAP_SEC) {
                pauses += PauseUpload(prev.timestamp.toEpochMilli(), r.timestamp.toEpochMilli())
            } else if (dt > 0) {
                distance += prev.speed!! * dt
            }
        }
        samples += SampleUpload(r.timestamp.toEpochMilli(), distance, r.speed!!, r.cadence)
    }

    return RunUpload(
        id = uid,
        exerciseType = RUNNING,
        start = s.startTime.toEpochMilli(),
        end = s.endTime.toEpochMilli(),
        utcOffsetMinutes = zoneOffset?.totalSeconds?.div(60),
        durationMs = s.duration.toMillis(),
        distanceM = s.distance,
        meanSpeedMps = s.meanSpeed,
        maxSpeedMps = s.maxSpeed,
        meanCadence = s.meanCadence,
        maxCadence = s.maxCadence,
        altitudeGainM = s.altitudeGain,
        altitudeLossM = s.altitudeLoss,
        minAltitudeM = s.minAltitude,
        maxAltitudeM = s.maxAltitude,
        vo2Max = s.vo2Max,
        pauses = pauses,
        route = s.route.orEmpty().sortedBy { it.timestamp }.map {
            RoutePointUpload(it.timestamp.toEpochMilli(), it.latitude, it.longitude, it.altitude, it.accuracy)
        },
        samples = samples,
    )
}

/** Talks to the Training Platform server on the home network. */
class ServerClient(private val baseUrl: String = BuildConfig.SERVER_URL, private val key: String = BuildConfig.UPLOAD_KEY) {

    private val gson = GsonBuilder().setFieldNamingPolicy(FieldNamingPolicy.LOWER_CASE_WITH_UNDERSCORES).create()

    val configured: Boolean get() = baseUrl.isNotBlank() && key.isNotBlank()

    /** Newest run start (epoch ms) the server already has, or null if it has none. */
    suspend fun latestStart(): Long? {
        val body = request("GET", "/api/samsung/latest", null)
        val v = JsonParser.parseString(body).asJsonObject.get("latest_start")
        return if (v == null || v.isJsonNull) null else v.asLong
    }

    /** Uploads one run. Returns the server's one-line verdict, e.g. "stored, 4 splits". */
    suspend fun upload(run: RunUpload): String {
        val body = request("POST", "/api/samsung/runs", gson.toJson(listOf(run)))
        val result = JsonParser.parseString(body).asJsonObject.getAsJsonArray("results")[0].asJsonObject
        return when (result.get("status").asString) {
            "stored" -> "stored, ${result.get("splits").asInt} splits, ${result.get("route_points").asInt} GPS points"
            else -> "skipped (${result.get("reason")?.asString ?: "no reason given"})"
        }
    }

    private suspend fun request(method: String, path: String, json: String?): String = withContext(Dispatchers.IO) {
        val conn = (URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 10_000
            readTimeout = 60_000
            setRequestProperty("X-API-Key", key)
            setRequestProperty("Accept", "application/json")
            if (json != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
            }
        }
        try {
            if (json != null) conn.outputStream.use { it.write(json.toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() } ?: ""
            if (code !in 200..299) throw IOException("Server answered $code: ${text.take(200)}")
            text
        } finally {
            conn.disconnect()
        }
    }
}
