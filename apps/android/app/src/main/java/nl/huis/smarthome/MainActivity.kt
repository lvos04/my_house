package nl.huis.smarthome

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import nl.huis.smarthome.ui.HuisRoot
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.theme.HuisTheme

class MainActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent {
            HuisTheme {
                val state by vm.state.collectAsStateWithLifecycle()
                HuisRoot(state = state, vm = vm)
            }
        }
    }

    override fun onStart() {
        super.onStart()
        vm.start()
    }

    override fun onStop() {
        vm.stop()
        super.onStop()
    }
}
