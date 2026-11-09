package nl.huis.smarthome.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import nl.huis.smarthome.R
import nl.huis.smarthome.core.Alert
import nl.huis.smarthome.core.LightGroup
import nl.huis.smarthome.core.PairParse
import nl.huis.smarthome.core.PairingCodec
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.data.UnpairResult
import nl.huis.smarthome.huisApp
import nl.huis.smarthome.work.AlertWorker

data class PairUi(val busy: Boolean = false, val error: String? = null)

/**
 * Thin layer between the screens and [nl.huis.smarthome.data.HomeRepository]. The repository
 * debounces and tracks commands and refuses them for viewer phones; this class only adds the
 * pairing-screen state and the "Ontkoppelen" flow.
 */
class MainViewModel(app: Application) : AndroidViewModel(app) {
    private val repo = app.huisApp.repository
    val state = repo.state
    val messages = repo.messages

    private val _pair = MutableStateFlow(PairUi())
    val pair: StateFlow<PairUi> = _pair.asStateFlow()

    private val _unpairing = MutableStateFlow(false)

    /** True while "Ontkoppelen" waits for the server (at most ~12 s). */
    val unpairing: StateFlow<Boolean> = _unpairing.asStateFlow()

    private val _unpairResult = MutableStateFlow<UnpairResult?>(null)

    /** Outcome of the last "Ontkoppelen", shown once on the pairing screen. */
    val unpairResult: StateFlow<UnpairResult?> = _unpairResult.asStateFlow()

    private val qrGate = QrGate()

    fun start() = repo.start()
    fun stop() = repo.stop()
    fun retry() = repo.retry()

    // ---- Climate (verbeterlijst 11, 13, 23) ----

    fun nudge(zone: Zone, delta: Double) = repo.nudgeTarget(zone, delta)
    fun setTarget(zone: Zone, value: Double) = repo.setTarget(zone, value)
    fun setMode(zone: Zone, mode: String) = repo.setMode(zone, mode)
    fun setFan(zone: Zone, fan: String) = repo.setFan(zone, fan)
    fun overrideFor(zone: Zone, minutes: Int) = repo.overrideFor(zone, minutes)
    fun override2h(zone: Zone) = overrideFor(zone, ZoneUi.DEFAULT_OVERRIDE_MINUTES)
    fun resume(zone: Zone) = repo.resume(zone)

    // ---- Lights and alerts ----

    fun toggle(group: LightGroup) = repo.toggleGroup(group)
    fun setBrightness(group: LightGroup, percent: Int) = repo.setGroupBrightness(group, percent)
    fun recallScene(group: LightGroup, sceneId: String) = repo.recallScene(group, sceneId)
    fun acknowledge(alert: Alert) = repo.acknowledge(alert)

    // ---- Energy ----

    fun loadEnergy(period: String = "day", date: String? = null) {
        viewModelScope.launch { repo.loadEnergy(period, date) }
    }

    /** For a periodic refresh that runs in the caller's coroutine (stops when the tab is hidden). */
    suspend fun refreshEnergy(period: String = "day", date: String? = null) = repo.loadEnergy(period, date)

    // ---- Connection settings (Meer) ----

    fun cfAccess(): Pair<String?, String?> = repo.store.cfAccess()

    fun saveCfAccess(id: String, secret: String) =
        repo.saveCfAccess(id.trim().ifEmpty { null }, secret.trim().ifEmpty { null })

    /** "Adressen bewerken": same rules as pairing. Returns a Dutch error, or null when saved. */
    fun updateAddresses(lan: String, remote: String): String? =
        repo.updateAddresses(lan.trim().ifEmpty { null }, remote.trim().ifEmpty { null })

    /** Notifications were just allowed: show the alerts that were waiting. */
    fun onNotificationsAllowed() = AlertWorker.runNow(getApplication())

    // ---- Pairing (verbeterlijst 17) ----

    /** Called with the raw text of a scanned QR code; repeats and rejected codes are ignored. */
    fun onQrScanned(text: String) {
        if (_pair.value.busy) return
        if (!qrGate.offer(text, System.currentTimeMillis())) return
        when (val r = PairingCodec.parse(text)) {
            is PairParse.Invalid -> {
                qrGate.reject(text)
                _pair.value = PairUi(error = r.reason)
            }
            // The QR code names the server id: the address is checked before the code is sent.
            is PairParse.Ok -> doPair(r, qrText = text)
        }
    }

    fun pairManually(address: String, code: String) {
        if (_pair.value.busy) return
        when (val r = PairingCodec.manual(address, code)) {
            is PairParse.Invalid -> _pair.value = PairUi(error = r.reason)
            is PairParse.Ok -> doPair(r, qrText = null)
        }
    }

    /** "Opnieuw scannen", or switching between scanning and manual entry. */
    fun clearPairError() {
        qrGate.reset()
        _pair.value = _pair.value.copy(error = null)
    }

    private fun doPair(parsed: PairParse.Ok, qrText: String?) {
        _unpairResult.value = null
        _pair.value = PairUi(busy = true)
        viewModelScope.launch {
            val error = try {
                repo.pair(parsed)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                getApplication<Application>().getString(R.string.pair_failed)
            }
            if (error != null && qrText != null) qrGate.reject(qrText)
            _pair.value = PairUi(busy = false, error = error)
        }
    }

    // ---- Unpairing (verbeterlijst 18) ----

    /** "Ontkoppelen": revoke on the server, then forget everything on this phone. */
    fun unpair() {
        if (_unpairing.value) return
        _unpairing.value = true
        viewModelScope.launch {
            val result = try {
                repo.unpairAndForget()
            } finally {
                _unpairing.value = false
            }
            qrGate.reset()
            _pair.value = PairUi()
            _unpairResult.value = result
        }
    }

    /** "Opnieuw koppelen" on the "niet meer gekoppeld" screen: the token is already dead. */
    fun forgetPairing() {
        repo.forgetPairing()
        qrGate.reset()
        _pair.value = PairUi()
        _unpairResult.value = null
    }

    fun clearUnpairResult() {
        _unpairResult.value = null
    }
}
