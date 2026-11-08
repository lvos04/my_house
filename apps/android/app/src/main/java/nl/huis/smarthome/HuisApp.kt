package nl.huis.smarthome

import android.app.Application
import nl.huis.smarthome.data.HomeRepository
import nl.huis.smarthome.data.SecureStore
import nl.huis.smarthome.work.AlertWorker

class HuisApp : Application() {
    lateinit var repository: HomeRepository
        private set

    override fun onCreate() {
        super.onCreate()
        repository = HomeRepository(this, SecureStore(this))
        AlertWorker.createChannel(this)
        if (repository.state.value.paired) AlertWorker.schedule(this)
    }
}

val android.content.Context.huisApp: HuisApp get() = applicationContext as HuisApp
