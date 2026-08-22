package link.lan.android

import android.app.Application
import android.util.Log
import link.lan.android.server.ServerCentre

/**
 * The application object.
 */
class LanLinkApplication : Application() {

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "LanLink ${BuildConfig.VERSION_NAME} starting")
        try {
            ServerCentre.init(this)
        } catch (e: Exception) {
            Log.e(TAG, "Could not initialize server", e)
        }
    }

    companion object {
        private const val TAG = "LanLink"
    }
}
