package nl.huis.smarthome.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import nl.huis.smarthome.core.Alert
import nl.huis.smarthome.core.LightGroup
import nl.huis.smarthome.core.PairParse
import nl.huis.smarthome.core.PairingCodec
import nl.huis.smarthome.core.UrlTools
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.huisApp
import nl.huis.smarthome.work.AlertWorker

data class PairUi(val busy: Boolean = false, val error: String? = null)

class MainViewModel(app: Application) : AndroidViewModel(app) {
    private val repo = app.huisApp.repository
    val state = repo.state
    val messages = repo.messages

    private val _pair = MutableStateFlow(PairUi())
    val pair: StateFlow<PairUi> = _pair.asStateFlow()

    fun start() = repo.start()
    fun stop() = repo.stop()
    fun retry() = repo.retry()

    fun nudge(zone: Zone, delta: Double) = repo.nudgeTarget(zone, delta)
    fun setMode(zone: Zone, mode: String) = repo.setMode(zone, mode)
    fun override2h(zone: Zone) = repo.overrideFor(zone, 120)
    fun resume(zone: Zone) = repo.resume(zone)
    fun toggle(group: LightGroup) = repo.toggleGroup(group)
    fun acknowledge(alert: Alert) = repo.acknowledge(alert)
    fun loadEnergy() = viewModelScope.launch { repo.loadEnergy("day") }

    fun cfAccess(): Pair<String?, String?> = repo.store.cfAccess()
    fun saveCfAccess(id: String, secret: String) = repo.saveCfAccess(id, secret)

    /** Called with the raw text of a scanned QR code. */
    fun onQrScanned(text: String) {
        if (_pair.value.busy) return
        when (val r = PairingCodec.parse(text)) {
            is PairParse.Invalid -> _pair.value = PairUi(error = r.reason)
            is PairParse.Ok -> doPair(r.lanUrl, r.remoteUrl, r.code)
        }
    }

    fun pairManually(address: String, code: String) {
        val base = UrlTools.normalizeBaseUrl(address)
        val normalizedCode = PairingCodec.normalizeCode(code)
        when {
            base == null -> _pair.value = PairUi(error = "Dit adres klopt niet.")
            normalizedCode == null -> _pair.value = PairUi(error = "Deze code klopt niet.")
            base.startsWith("https://") -> doPair(null, base, normalizedCode)
            else -> doPair(base, null, normalizedCode)
        }
    }

    fun clearPairError() {
        _pair.value = _pair.value.copy(error = null)
    }

    private fun doPair(lan: String?, remote: String?, code: String) {
        _pair.value = PairUi(busy = true)
        viewModelScope.launch {
            val error = repo.pair(lan, remote, code)
            _pair.value = PairUi(busy = false, error = error)
            if (error == null) AlertWorker.schedule(getApplication())
        }
    }

    fun unpair() {
        repo.unpair()
        AlertWorker.cancel(getApplication())
    }
}
