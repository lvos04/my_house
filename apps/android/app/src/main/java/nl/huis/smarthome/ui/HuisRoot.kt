package nl.huis.smarthome.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.StringRes
import androidx.compose.foundation.layout.Box
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import nl.huis.smarthome.R
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.screens.ClimateScreen
import nl.huis.smarthome.ui.screens.EnergyScreen
import nl.huis.smarthome.ui.screens.HomeScreen
import nl.huis.smarthome.ui.screens.MoreScreen
import nl.huis.smarthome.ui.screens.PairScreen
import nl.huis.smarthome.ui.theme.Huis

private enum class Tab(@StringRes val label: Int, val icon: ImageVector) {
    START(R.string.nav_start, Icons.Outlined.Home),
    CLIMATE(R.string.nav_climate, Icons.Outlined.Thermostat),
    ENERGY(R.string.nav_energy, Icons.Outlined.Bolt),
    MORE(R.string.nav_more, Icons.Outlined.MoreHoriz),
}

@Composable
fun HuisRoot(state: HomeState, vm: MainViewModel) {
    if (!state.paired) {
        PairScreen(vm)
        return
    }

    var tab by rememberSaveable { mutableIntStateOf(0) }
    var selectedZone by rememberSaveable { mutableStateOf<String?>(null) }
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(Unit) {
        vm.messages.collect { snackbar.showSnackbar(it) }
    }

    // Ask once for notification permission (Android 13+) so alert notifications can be shown.
    val notifLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

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
        Box(Modifier.padding(padding)) {
            when (Tab.entries[tab]) {
                Tab.START -> HomeScreen(
                    state = state,
                    vm = vm,
                    onOpenZone = { selectedZone = it; tab = Tab.CLIMATE.ordinal },
                    onOpenAlerts = { tab = Tab.MORE.ordinal },
                )
                Tab.CLIMATE -> ClimateScreen(
                    state = state,
                    vm = vm,
                    selectedZoneId = selectedZone,
                    onSelectZone = { selectedZone = it },
                    onBack = { tab = Tab.START.ordinal },
                )
                Tab.ENERGY -> EnergyScreen(state = state, vm = vm)
                Tab.MORE -> MoreScreen(state = state, vm = vm)
            }
        }
    }
}
