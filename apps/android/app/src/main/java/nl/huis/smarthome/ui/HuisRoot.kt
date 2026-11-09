package nl.huis.smarthome.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.StringRes
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Bolt
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material.icons.outlined.Thermostat
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import nl.huis.smarthome.R
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.screens.ClimateScreen
import nl.huis.smarthome.ui.screens.EnergyScreen
import nl.huis.smarthome.ui.screens.HomeScreen
import nl.huis.smarthome.ui.screens.MoreScreen
import nl.huis.smarthome.ui.screens.PairScreen
import nl.huis.smarthome.ui.screens.UnpairedScreen
import nl.huis.smarthome.ui.theme.Huis
import nl.huis.smarthome.work.AlertNotifier

private enum class Tab(@StringRes val label: Int, val icon: ImageVector) {
    START(R.string.nav_start, Icons.Outlined.Home),
    CLIMATE(R.string.nav_climate, Icons.Outlined.Thermostat),
    ENERGY(R.string.nav_energy, Icons.Outlined.Bolt),
    MORE(R.string.nav_more, Icons.Outlined.MoreHoriz),
}

/** Phone notifications as the screens need them (start-screen card and Meer). */
data class NotifUi(
    val canPost: Boolean,
    val prompt: NotifPrompt,
    val action: NotifAction,
    val waiting: Int,
    /** Ask Android (or open the settings when it no longer asks). */
    val onAllow: () -> Unit,
    /** "Niet nu": the explanation is not shown again on its own. */
    val onLater: () -> Unit,
    val onSettings: () -> Unit,
)

@Composable
fun HuisRoot(state: HomeState, vm: MainViewModel) {
    // Revoked, expired or unknown token (verbeterlijst 18): never "Geen verbinding", never the old token again.
    if (state.unpaired != null) {
        UnpairedScreen(state, onRepair = vm::forgetPairing)
        return
    }
    if (!state.paired) {
        PairScreen(vm, storeWasReset = state.storeWasReset)
        return
    }

    var tab by rememberSaveable { mutableIntStateOf(0) }
    var selectedZone by rememberSaveable { mutableStateOf<String?>(null) }
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(Unit) {
        vm.messages.collect { snackbar.showSnackbar(it) }
    }

    // Back from Klimaat, Energie or Meer goes to Start before leaving the app.
    BackHandler(enabled = tab != Tab.START.ordinal) { tab = Tab.START.ordinal }

    val notif = rememberNotifUi(vm, state.notificationsWaiting)

    Scaffold(
        containerColor = Huis.colors.ground,
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            NavigationBar(containerColor = Huis.colors.surface) {
                Tab.entries.forEachIndexed { i, t ->
                    NavigationBarItem(
                        selected = tab == i,
                        onClick = { tab = i },
                        icon = { Icon(t.icon, contentDescription = null) },
                        label = { Text(stringResource(t.label), style = MaterialTheme.typography.labelMedium) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = Huis.colors.ink,
                            selectedTextColor = Huis.colors.ink,
                            indicatorColor = Huis.colors.surfaceMuted,
                            unselectedIconColor = Huis.colors.muted,
                            unselectedTextColor = Huis.colors.muted,
                        ),
                    )
                }
            }
        },
    ) { padding ->
        // consumeWindowInsets: imePadding() further down then only adds what the keyboard covers
        // beyond the bottom bar.
        Box(Modifier.padding(padding).consumeWindowInsets(padding)) {
            when (Tab.entries[tab]) {
                Tab.START -> HomeScreen(
                    state = state,
                    vm = vm,
                    onOpenZone = { selectedZone = it; tab = Tab.CLIMATE.ordinal },
                    onOpenAlerts = { tab = Tab.MORE.ordinal },
                    notif = notif,
                )
                Tab.CLIMATE -> ClimateScreen(
                    state = state,
                    vm = vm,
                    selectedZoneId = selectedZone,
                    onSelectZone = { selectedZone = it },
                    onBack = { tab = Tab.START.ordinal },
                )
                Tab.ENERGY -> EnergyScreen(state = state, vm = vm)
                Tab.MORE -> MoreScreen(state = state, vm = vm, notif = notif)
            }
        }
    }
}

/**
 * Notification permission (Android 13+) is never asked on start. The start screen first explains
 * why; Android is only asked when the user taps "Meldingen toestaan" (once per install, again
 * only from that button in Meer). After "niet meer vragen" the button opens the settings.
 * Alerts that were waiting are shown as soon as notifications are allowed.
 */
@Composable
private fun rememberNotifUi(vm: MainViewModel, waiting: Int): NotifUi {
    val context = LocalContext.current
    val prefs = remember { UiPrefs(context) }
    val runtimePermission = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
    var canPost by remember { mutableStateOf(AlertNotifier.canPost(context)) }
    var explained by remember { mutableStateOf(prefs.notificationsExplained) }
    // Recomposition trigger after the system dialog (rationale state changes with it).
    var askedTick by remember { mutableIntStateOf(0) }

    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        canPost = AlertNotifier.canPost(context)
        askedTick++
        if (granted) vm.onNotificationsAllowed()
    }

    // Back from the system settings: check again and show what was waiting.
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                val now = AlertNotifier.canPost(context)
                if (now && !canPost) vm.onNotificationsAllowed()
                canPost = now
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    // Read again after every answer to the system dialog and every return to the app.
    val action = remember(askedTick, canPost) {
        val granted = !runtimePermission ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        val rationale = runtimePermission &&
            context.findActivity()?.shouldShowRequestPermissionRationale(Manifest.permission.POST_NOTIFICATIONS) == true
        NotificationPolicy.action(runtimePermission, granted, prefs.notificationsAsked, rationale)
    }

    val openSettings: () -> Unit = {
        if (!context.startSafely(AlertNotifier.settingsIntent(context))) context.startSafely(appSettingsIntent(context))
    }
    val allow: () -> Unit = {
        prefs.notificationsExplained = true
        explained = true
        if (action == NotifAction.REQUEST) {
            prefs.notificationsAsked = true
            launcher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            openSettings()
        }
    }
    return NotifUi(
        canPost = canPost,
        prompt = NotificationPolicy.prompt(canPost, runtimePermission, explained, waiting),
        action = action,
        waiting = waiting,
        onAllow = allow,
        onLater = {
            prefs.notificationsExplained = true
            explained = true
        },
        onSettings = openSettings,
    )
}
