package com.trainingplatform.trainingsync

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import com.samsung.android.sdk.health.data.HealthDataService
import com.samsung.android.sdk.health.data.HealthDataStore
import com.samsung.android.sdk.health.data.error.HealthDataException
import com.samsung.android.sdk.health.data.error.ResolvablePlatformException
import com.samsung.android.sdk.health.data.permission.AccessType
import com.samsung.android.sdk.health.data.permission.Permission
import com.samsung.android.sdk.health.data.request.DataTypes
import com.trainingplatform.trainingsync.ui.theme.TrainingSyncTheme
import kotlinx.coroutines.launch
import java.time.Duration
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** Step 4: read recent runs from Samsung Health and show what we got. Nothing is uploaded yet. */
class MainActivity : ComponentActivity() {

    private val store: HealthDataStore by lazy { HealthDataService.getStore(applicationContext) }

    private val exercisePermission = Permission.of(DataTypes.EXERCISE, AccessType.READ)
    private val routePermission = Permission.of(DataTypes.EXERCISE_LOCATION, AccessType.READ)
    private val permissions = setOf(exercisePermission, routePermission)

    private var status by mutableStateOf("Tap the button to read your last 30 days of runs from Samsung Health.")
    private var runs by mutableStateOf<List<RunSummary>>(emptyList())
    private var busy by mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            TrainingSyncTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { padding ->
                    RunListScreen(status, runs, busy, onRead = ::readRuns, modifier = Modifier.padding(padding))
                }
            }
        }
    }

    private fun readRuns() {
        lifecycleScope.launch {
            busy = true
            try {
                var granted = store.getGrantedPermissions(permissions)
                if (!granted.containsAll(permissions)) {
                    status = "Asking Samsung Health for permission…"
                    granted = store.requestPermissions(permissions, this@MainActivity)
                }
                if (exercisePermission !in granted) {
                    status = "Samsung Health didn't allow reading exercise data, so there's nothing to show."
                    return@launch
                }
                status = "Reading runs…"
                runs = RunReader(store).recentRuns(days = 30)
                status = buildString {
                    append("Found ${runs.size} run${if (runs.size == 1) "" else "s"} in the last 30 days.")
                    if (routePermission !in granted) append(" Route access wasn't allowed, so GPS points show as 0.")
                }
            } catch (e: ResolvablePlatformException) {
                status = "Samsung Health needs attention (${e.errorCode}): ${e.errorMessage}"
                if (e.hasResolution) e.resolve(this@MainActivity)
            } catch (e: HealthDataException) {
                status = "Samsung Health error ${e.errorCode}: ${e.errorMessage}"
            } catch (e: Exception) {
                status = "Unexpected error: ${e.javaClass.simpleName}: ${e.message}"
            } finally {
                busy = false
            }
        }
    }
}

private val dateFormat = DateTimeFormatter.ofPattern("EEE, MMM d · h:mm a", Locale.US)

private fun Duration.clock(): String {
    val s = seconds
    return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, (s % 3600) / 60, s % 60) else "%d:%02d".format(s / 60, s % 60)
}

private fun miles(m: Double) = "%.2f mi".format(m / 1609.344)

@Composable
fun RunListScreen(
    status: String,
    runs: List<RunSummary>,
    busy: Boolean,
    onRead: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Training Sync", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.padding(top = 8.dp))
        Text(status, style = MaterialTheme.typography.bodyMedium)
        Button(onClick = onRead, enabled = !busy) { Text(if (busy) "Reading…" else "Read runs from Samsung Health") }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(runs, key = { it.uid }) { RunCard(it) }
        }
    }
}

@Composable
fun RunCard(run: RunSummary) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            val when_ = run.start.atZone(run.zoneOffset ?: ZoneId.systemDefault())
            Text(dateFormat.format(when_), style = MaterialTheme.typography.titleSmall)
            Text(
                "${run.distanceM?.let { miles(it.toDouble()) } ?: "no distance"} · moving ${run.moving.clock()} " +
                    "· Start→Stop ${run.wallClock.clock()}"
            )
            Text("GPS points: ${run.routePoints} · readings: ${run.logEntries} " +
                "(speed ${run.speedReadings}, cadence ${run.cadenceReadings})")
            Text("Typical gap between speed readings: %.1f s · gaps over 5 s total %.0f s (Start→Stop minus moving: %d s)"
                .format(run.medianGapSec, run.longGapsSec, run.pausedByClock.seconds),
                style = MaterialTheme.typography.bodySmall)
            Text("Distance from speed readings: ${miles(run.distanceFromSpeedM)}", style = MaterialTheme.typography.bodySmall)
            Text("id: ${run.uid}", style = MaterialTheme.typography.labelSmall)
        }
    }
}
