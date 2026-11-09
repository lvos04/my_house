package nl.huis.smarthome.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.LinkOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import nl.huis.smarthome.R
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.components.HuisCard
import nl.huis.smarthome.ui.components.NeutralNote
import nl.huis.smarthome.ui.theme.Huis

/**
 * "Deze telefoon is niet meer gekoppeld" (verbeterlijst 18): shown instead of "Geen verbinding"
 * when the server revoked or expired the token, or no longer knows it. The old token is never
 * sent again; "Opnieuw koppelen" wipes it here and opens the pairing screen.
 */
@Composable
fun UnpairedScreen(state: HomeState, onRepair: () -> Unit) {
    val reason = state.unpaired
    Column(
        Modifier
            .fillMaxSize()
            .background(Huis.colors.ground)
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 32.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        Icon(Icons.Outlined.LinkOff, contentDescription = null, tint = Huis.colors.warn, modifier = Modifier.size(40.dp))
        Text(
            stringResource(R.string.unpaired_title),
            style = MaterialTheme.typography.headlineMedium,
            color = Huis.colors.ink,
            modifier = Modifier.semantics { heading() },
        )
        HuisCard(radius = 20) {
            // The reason comes from the app's data layer (revoked, expired, unknown after a restore …).
            reason?.let { Text(it.message, style = MaterialTheme.typography.bodyLarge, color = Huis.colors.ink) }
            (state.overview?.home?.name ?: state.pairing?.homeName)?.let {
                Text(stringResource(R.string.unpaired_home, it), style = MaterialTheme.typography.bodyMedium, color = Huis.colors.muted)
            }
        }
        NeutralNote(stringResource(R.string.unpaired_note))
        Button(
            onClick = onRepair,
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(containerColor = Huis.colors.ink, contentColor = Huis.colors.ground),
        ) { Text(stringResource(R.string.unpaired_repair)) }
    }
}
