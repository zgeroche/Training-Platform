package com.trainingplatform.trainingsync

import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.samsung.android.sdk.health.data.HealthDataService
import com.samsung.android.sdk.health.data.permission.AccessType
import com.samsung.android.sdk.health.data.permission.Permission
import com.samsung.android.sdk.health.data.request.DataTypes
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Background check, about every 15 minutes (Android's minimum) and only on Wi-Fi: read Samsung
 * Health on the phone, ask the PC for its newest run, and upload anything newer. Most runs of
 * this find nothing new and cost one tiny request.
 *
 * It never asks for permissions (that needs the app on screen); the Sync to PC button does that.
 * Problems are logged and the next check simply tries again: no retries that would drain battery.
 */
class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val store = HealthDataService.getStore(applicationContext)
        val needed = setOf(Permission.of(DataTypes.EXERCISE, AccessType.READ))
        try {
            if (!store.getGrantedPermissions(needed).containsAll(needed)) {
                Log.w(TAG, "Background sync skipped: open the app and allow Samsung Health access.")
                return Result.success()
            }
            val outcome = RunSync(store).syncNewRuns()
            Log.i(TAG, "Background sync: ${outcome.message.replace('\n', ' ')}")
        } catch (e: IOException) {
            Log.i(TAG, "Background sync: PC not reachable (${e.message}); will try again next time.")
        } catch (e: Exception) {
            Log.w(TAG, "Background sync failed; will try again next time.", e)
        }
        return Result.success()
    }

    companion object {
        const val TAG = "TrainingSync"
        private const val WORK_NAME = "background-run-sync"

        /** Safe to call every time the app opens: an already-scheduled check is kept, not restarted. */
        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.UNMETERED).build())
                .build()
            WorkManager.getInstance(context)
                .enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request)
        }
    }
}
