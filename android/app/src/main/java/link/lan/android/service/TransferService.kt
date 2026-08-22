package link.lan.android.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import link.lan.android.MainActivity
import link.lan.android.R
import link.lan.android.data.TransferCentre

/**
 * Keeps transfers running while the phone is doing something else.
 *
 * Android will freeze the process a moment after the app leaves the screen, and
 * a 2 GB file does not care that somebody wanted to answer a message. The
 * service exists for exactly that window and stops itself the moment the queue
 * is empty — a notification that outlives the work is a notification people
 * learn to swipe away.
 *
 * On Android 14 a foreground service must declare a type and hold the matching
 * permission; `dataSync` is the one that fits moving files between devices.
 */
class TransferService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
        startInForeground("Starting…")

        scope.launch {
            TransferCentre.summary.collectLatest { summary ->
                if (!TransferCentre.hasActiveWork) {
                    Log.i(TAG, "queue is empty; stopping the transfer service")
                    stopSelf()
                    return@collectLatest
                }
                notify(summary)
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Restarting with no work would put a notification on screen for nothing.
        if (!TransferCentre.hasActiveWork) {
            stopSelf()
            return START_NOT_STICKY
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun startInForeground(text: String) {
        val notification = build(text)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(ID, notification)
        }
    }

    private fun notify(text: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(ID, build(text))
    }

    private fun build(text: String): Notification {
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL)
            .setContentTitle("LanLink")
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    private fun createChannel() {
        val channel = NotificationChannel(
            CHANNEL,
            "File transfers",
            // Low: it should be visible while it runs, not demand attention.
            NotificationManager.IMPORTANCE_LOW,
        )
        channel.description = "Shown while LanLink is moving files."
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    companion object {
        private const val TAG = "LanLinkTransfer"
        private const val CHANNEL = "lanlink-transfers"
        private const val ID = 1

        /** Start only when there is something to do. */
        fun ensureRunning(context: Context) {
            if (!TransferCentre.hasActiveWork) return
            val intent = Intent(context, TransferService::class.java)
            runCatching { context.startForegroundService(intent) }
                .onFailure { Log.w(TAG, "the transfer service could not start", it) }
        }
    }
}
