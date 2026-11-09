package nl.huis.smarthome

import android.app.Application
import nl.huis.smarthome.data.HomeRepository
import nl.huis.smarthome.data.NetworkWatcher
import nl.huis.smarthome.data.SecureStore
import nl.huis.smarthome.work.AlertWorker

class HuisApp : Application() {
    lateinit var repository: HomeRepository
        private set

    override fun onCreate() {
        super.onCreate()
        // Never throws: a damaged Keystore entry resets the store (re-pair) instead of crashing
        // the app, the widget and the background worker on every start.
        repository = HomeRepository(this, SecureStore.open(this))
        AlertWorker.createChannel(this)
        val s = repository.state.value
        if (s.paired && s.unpaired == null) AlertWorker.schedule(this)
        // Forget the route on every network switch (home wifi ↔ mobile data).
        NetworkWatcher.register(this) { repository.onNetworkChanged() }
    }
}

val android.content.Context.huisApp: HuisApp get() = applicationContext as HuisApp
