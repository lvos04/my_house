package nl.huis.smarthome.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.util.Log
import nl.huis.smarthome.core.NetworkChangeDetector

/**
 * Tells the repository when the phone switches network (wifi at home ↔ mobile data), so the
 * chosen route is forgotten and picked again: the home address first (verbeterlijst 37).
 */
object NetworkWatcher {
    @Volatile private var registered: ConnectivityManager.NetworkCallback? = null

    fun register(context: Context, onChange: () -> Unit) {
        if (registered != null) return
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return
        val detector = NetworkChangeDetector()
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                if (detector.onAvailable(network.networkHandle)) onChange()
            }

            override fun onLost(network: Network) {
                if (detector.onLost(network.networkHandle)) onChange()
            }
        }
        try {
            cm.registerDefaultNetworkCallback(callback)
            registered = callback
        } catch (e: RuntimeException) {
            // SecurityException without ACCESS_NETWORK_STATE, or too many callbacks: the app still
            // works, it only re-picks the route after a failed request.
            Log.w("NetworkWatcher", "Netwerkwissels volgen lukt niet", e)
        }
    }
}
