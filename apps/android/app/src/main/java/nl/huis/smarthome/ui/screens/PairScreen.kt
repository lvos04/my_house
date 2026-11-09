package nl.huis.smarthome.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import androidx.annotation.DrawableRes
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.OptIn
import androidx.camera.core.CameraSelector
import androidx.camera.core.CameraState
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.LiveData
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import nl.huis.smarthome.R
import nl.huis.smarthome.data.UnpairResult
import nl.huis.smarthome.ui.CameraIssue
import nl.huis.smarthome.ui.CameraPolicy
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.MeldingSoort
import nl.huis.smarthome.ui.components.HuisCard
import nl.huis.smarthome.ui.components.KnopSoort
import nl.huis.smarthome.ui.components.Note
import nl.huis.smarthome.ui.components.PeilIcoon
import nl.huis.smarthome.ui.components.PeilKnop
import nl.huis.smarthome.ui.components.PeilVorm
import nl.huis.smarthome.ui.components.meldingKleur
import nl.huis.smarthome.ui.components.meldingRand
import nl.huis.smarthome.ui.components.stippelRand
import nl.huis.smarthome.ui.theme.Huis
import nl.huis.smarthome.ui.theme.PeilTokens
import nl.huis.smarthome.ui.UiPrefs
import nl.huis.smarthome.ui.appSettingsIntent
import nl.huis.smarthome.ui.findActivity
import nl.huis.smarthome.ui.startSafely
import java.util.concurrent.Executors

// The pairing screen follows the system theme like the rest of the app (Peil tokens, light and dark).

@Composable
fun PairScreen(vm: MainViewModel, storeWasReset: Boolean = false) {
    val pair by vm.pair.collectAsStateWithLifecycle()
    val unpairResult by vm.unpairResult.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val prefs = remember { UiPrefs(context) }
    var manual by rememberSaveable { mutableStateOf(false) }
    var cameraIssue by rememberSaveable { mutableStateOf<CameraIssue?>(null) }
    val hasCameraHardware = remember { context.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY) }

    fun cameraGranted() = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
    var hasCamera by remember { mutableStateOf(cameraGranted()) }
    var answers by remember { mutableIntStateOf(0) }
    val cameraLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        hasCamera = it
        answers++
    }

    // Back from the app settings: the camera may be allowed now.
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) hasCamera = cameraGranted()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    // After "niet meer vragen" Android shows nothing any more: then the button opens the settings.
    val cameraBlocked = remember(hasCamera, answers) {
        val rationale = context.findActivity()?.shouldShowRequestPermissionRationale(Manifest.permission.CAMERA) == true
        CameraPolicy.openSettings(hasCamera, prefs.cameraAsked, rationale)
    }
    val requestCamera: () -> Unit = {
        if (cameraBlocked) {
            context.startSafely(appSettingsIntent(context))
        } else {
            prefs.cameraAsked = true
            cameraLauncher.launch(Manifest.permission.CAMERA)
        }
    }
    val switchMode: () -> Unit = {
        manual = !manual
        vm.clearPairError()
    }

    val p = Huis.peil
    Column(
        Modifier
            .fillMaxSize()
            .background(p.background)
            // Includes the keyboard: the manual fields stay visible.
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 24.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(stringResource(R.string.pair_step), color = p.mutedForeground, style = MaterialTheme.typography.bodySmall)
            Text(
                stringResource(R.string.pair_title),
                color = p.foreground,
                style = MaterialTheme.typography.headlineSmall,
                modifier = Modifier.semantics { heading() },
            )
            Text(stringResource(R.string.pair_body), color = p.mutedForeground, style = MaterialTheme.typography.bodyLarge)
        }

        when (unpairResult) {
            // Done is neutral (a check), something left to do is Beperkt; never green for "done".
            UnpairResult.REVOKED_ON_SERVER -> Note(stringResource(R.string.unpair_revoked), MeldingSoort.NEUTRAAL, icoon = R.drawable.lucide_check)
            UnpairResult.LOCAL_ONLY -> Note(stringResource(R.string.unpair_local_only), MeldingSoort.BEPERKT)
            null -> if (storeWasReset) Note(stringResource(R.string.store_reset), MeldingSoort.BEPERKT)
        }

        if (manual) {
            ManualEntry(busy = pair.busy, onSubmit = vm::pairManually)
        } else {
            ScannerPanel(
                hasCamera = hasCamera,
                cameraBlocked = cameraBlocked,
                cameraIssue = if (hasCameraHardware) cameraIssue else CameraIssue.UNAVAILABLE,
                canRetryCamera = CameraPolicy.canRetry(cameraIssue, hasCameraHardware),
                busy = pair.busy,
                onRequestCamera = requestCamera,
                onCameraError = { cameraIssue = it },
                onRetryCamera = { cameraIssue = null },
                onManual = switchMode,
                onScanned = vm::onQrScanned,
            )
        }

        pair.error?.let { error ->
            // Foutpaneel (Peil): a refused code or a different server answering is a measured mismatch.
            val k = meldingKleur(MeldingSoort.STORING)
            Column(
                Modifier
                    .fillMaxWidth()
                    .clip(PeilVorm)
                    .background(k.vlak)
                    .meldingRand(k)
                    .semantics { liveRegion = LiveRegionMode.Assertive }
                    .padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    PeilIcoon(R.drawable.lucide_octagon_x, k.icoon, Modifier.padding(top = 2.dp))
                    Text(error, color = k.inkt, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                }
                if (!manual) {
                    // The same rejected QR code is not sent again until the user asks for it.
                    PeilKnop(
                        stringResource(R.string.pair_scan_again),
                        onClick = vm::clearPairError,
                        soort = KnopSoort.SECUNDAIR,
                        icoon = R.drawable.lucide_rotate_ccw,
                        modifier = Modifier.align(Alignment.End),
                    )
                }
            }
        }

        HuisCard {
            RouteLine(stringResource(R.string.route_lan), R.drawable.lucide_house, stringResource(R.string.pair_route_lan))
            RouteLine(stringResource(R.string.route_remote), R.drawable.lucide_globe, stringResource(R.string.pair_route_remote))
            Text(stringResource(R.string.pair_route_info), color = p.mutedForeground, style = MaterialTheme.typography.bodySmall)
        }

        PeilKnop(
            stringResource(if (manual) R.string.pair_scan_instead else R.string.pair_manual),
            onClick = switchMode,
            soort = KnopSoort.SECUNDAIR,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

/** "Thuis — Lokaal adres, het snelst": the route with its icon (neutral: a route is not a health status). */
@Composable
private fun RouteLine(label: String, @DrawableRes icoon: Int, text: String) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Top) {
        PeilIcoon(icoon, Huis.peil.foreground, Modifier.padding(top = 1.dp), size = 18.dp)
        Text(label, color = Huis.peil.foreground, style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.SemiBold))
        Text(text, color = Huis.peil.mutedForeground, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun ScannerPanel(
    hasCamera: Boolean,
    cameraBlocked: Boolean,
    cameraIssue: CameraIssue?,
    canRetryCamera: Boolean,
    busy: Boolean,
    onRequestCamera: () -> Unit,
    onCameraError: (CameraIssue) -> Unit,
    onRetryCamera: () -> Unit,
    onManual: () -> Unit,
    onScanned: (String) -> Unit,
) {
    val previewLabel = stringResource(R.string.camera_preview)
    val p = Huis.peil
    // No picture (camera not allowed or not available) is Onbekend: dashed, with the reason; never red.
    val noPicture = cameraIssue != null || !hasCamera
    Box(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 320.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(if (noPicture) p.unknownBg else p.muted)
            .then(if (noPicture) Modifier.stippelRand(p.unknown, radius = 12.dp) else Modifier),
        contentAlignment = Alignment.Center,
    ) {
        when {
            cameraIssue != null -> CameraMessage(
                text = stringResource(
                    when (cameraIssue) {
                        CameraIssue.IN_USE -> R.string.pair_camera_in_use
                        CameraIssue.DISABLED -> R.string.pair_camera_disabled
                        CameraIssue.UNAVAILABLE -> R.string.pair_camera_error
                    },
                ),
                button = stringResource(R.string.pair_manual_short),
                onClick = onManual,
                secondary = if (canRetryCamera) stringResource(R.string.pair_camera_retry) else null,
                onSecondary = if (canRetryCamera) onRetryCamera else null,
                announce = true,
            )
            !hasCamera -> CameraMessage(
                text = stringResource(if (cameraBlocked) R.string.pair_camera_blocked else R.string.pair_camera_needed),
                button = stringResource(R.string.pair_camera_allow),
                onClick = onRequestCamera,
                secondary = stringResource(R.string.pair_manual_short),
                onSecondary = onManual,
            )
            else -> {
                QrCamera(
                    Modifier
                        .matchParentSize()
                        .semantics { contentDescription = previewLabel },
                    onResult = onScanned,
                    onError = onCameraError,
                )
                // Over the camera picture: a white frame in both themes (a dark frame would vanish on a dark picture).
                Box(
                    Modifier
                        .size(220.dp)
                        .border(4.dp, PeilTokens.Licht.card, RoundedCornerShape(12.dp)),
                )
                Box(
                    Modifier
                        .size(width = 188.dp, height = 2.dp)
                        .background(p.primary),
                )
                if (busy) {
                    Column(
                        Modifier
                            .clip(PeilVorm)
                            .background(p.card)
                            .border(BorderStroke(1.dp, p.border), PeilVorm)
                            .padding(16.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        CircularProgressIndicator(color = p.primary)
                        Text(stringResource(R.string.pair_busy), color = p.foreground, style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
        }
    }
}

@Composable
private fun CameraMessage(
    text: String,
    button: String,
    onClick: () -> Unit,
    secondary: String? = null,
    onSecondary: (() -> Unit)? = null,
    /** Read out by TalkBack when it appears (a camera error replaces the picture). */
    announce: Boolean = false,
) {
    val p = Huis.peil
    Column(
        Modifier.padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        PeilIcoon(R.drawable.lucide_camera_off, p.unknown, size = 32.dp)
        Text(
            text,
            color = p.unknownInk,
            style = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
            modifier = if (announce) Modifier.semantics { liveRegion = LiveRegionMode.Polite } else Modifier,
        )
        PeilKnop(button, onClick = onClick, soort = KnopSoort.PRIMAIR)
        if (secondary != null && onSecondary != null) {
            PeilKnop(secondary, onClick = onSecondary, soort = KnopSoort.STIL)
        }
    }
}

@Composable
private fun ManualEntry(busy: Boolean, onSubmit: (String, String) -> Unit) {
    var address by rememberSaveable { mutableStateOf("") }
    var code by rememberSaveable { mutableStateOf("") }
    val focus = LocalFocusManager.current
    val canSubmit = !busy && address.isNotBlank() && code.isNotBlank()
    val submit: () -> Unit = {
        if (canSubmit) {
            focus.clearFocus()
            onSubmit(address, code)
        }
    }
    val colors = huisFieldColors()
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedTextField(
            value = address,
            onValueChange = { address = it },
            label = { Text(stringResource(R.string.pair_address)) },
            placeholder = { Text(stringResource(R.string.pair_address_hint)) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
            colors = colors,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = code,
            onValueChange = { code = it },
            label = { Text(stringResource(R.string.pair_code)) },
            placeholder = { Text(stringResource(R.string.pair_code_hint)) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { submit() }),
            colors = colors,
            modifier = Modifier.fillMaxWidth(),
        )
        PeilKnop(
            stringResource(if (busy) R.string.pair_busy else R.string.pair_submit),
            onClick = submit,
            enabled = canSubmit,
            soort = KnopSoort.PRIMAIR,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

/**
 * CameraX preview + ML Kit QR analysis bound to the current lifecycle. A camera that cannot be
 * started calls [onError] instead of leaving a black box: binding can throw (no camera), but a
 * camera that is in use by another app, disabled, or failing in its driver only says so through
 * its state afterwards, so that state is observed as well.
 */
@Composable
private fun QrCamera(modifier: Modifier, onResult: (String) -> Unit, onError: (CameraIssue) -> Unit) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val currentOnResult by rememberUpdatedState(onResult)
    val currentOnError by rememberUpdatedState(onError)
    val executor = remember { Executors.newSingleThreadExecutor() }
    val scanner = remember {
        BarcodeScanning.getClient(BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build())
    }
    val providerFuture = remember { runCatching { ProcessCameraProvider.getInstance(context) }.getOrNull() }
    // The bound camera's state, so the observer can be removed when this leaves the screen.
    val bound = remember { BoundCamera() }

    if (providerFuture == null) {
        DisposableEffect(Unit) {
            currentOnError(CameraIssue.UNAVAILABLE)
            onDispose { }
        }
        return
    }

    AndroidView(
        modifier = modifier,
        factory = { ctx ->
            val view = PreviewView(ctx).apply { scaleType = PreviewView.ScaleType.FILL_CENTER }
            providerFuture.addListener({
                if (bound.disposed) return@addListener
                try {
                    val provider = providerFuture.get()
                    val preview = Preview.Builder().build().also { it.setSurfaceProvider(view.surfaceProvider) }
                    val analysis = ImageAnalysis.Builder()
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build()
                        .also { it.setAnalyzer(executor) { proxy -> analyze(proxy, scanner) { value -> currentOnResult(value) } } }
                    provider.unbindAll()
                    val camera = provider.bindToLifecycle(lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
                    val state = camera.cameraInfo.cameraState
                    bound.state = state
                    state.observe(lifecycleOwner) { s ->
                        val error = s.error ?: return@observe
                        if (error.type == CameraState.ErrorType.CRITICAL) {
                            // Disabled, a fatal driver error, stream setup failed: CameraX gives up.
                            currentOnError(CameraPolicy.issueFor(error.code))
                        } else {
                            // CameraX retries a recoverable error itself ("in use" can be brief).
                            // If the camera has still not opened a moment later while the screen is
                            // visible (it may wait in PENDING_OPEN without an error by then), say why
                            // instead of leaving a black picture.
                            view.postDelayed({
                                val latest = state.value
                                val visible = lifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)
                                if (!bound.disposed && visible && latest != null && latest.type != CameraState.Type.OPEN) {
                                    currentOnError(CameraPolicy.issueFor(latest.error?.code ?: error.code))
                                }
                            }, RECOVERABLE_GRACE_MS)
                        }
                    }
                } catch (e: Exception) {
                    currentOnError(CameraIssue.UNAVAILABLE)
                }
            }, ContextCompat.getMainExecutor(ctx))
            view
        },
    )

    DisposableEffect(Unit) {
        onDispose {
            bound.disposed = true
            bound.state?.removeObservers(lifecycleOwner)
            bound.state = null
            if (providerFuture.isDone) runCatching { providerFuture.get().unbindAll() }
            scanner.close()
            executor.shutdown()
        }
    }
}

/** How long a recoverable camera error ("in use") may keep the camera closed before the scanner says so. */
private const val RECOVERABLE_GRACE_MS = 2_000L

/** What QrCamera bound, kept across recompositions. */
private class BoundCamera {
    var state: LiveData<CameraState>? = null
    var disposed = false
}

@OptIn(ExperimentalGetImage::class)
private fun analyze(
    proxy: ImageProxy,
    scanner: com.google.mlkit.vision.barcode.BarcodeScanner,
    onResult: (String) -> Unit,
) {
    val media = proxy.image
    if (media == null) {
        proxy.close()
        return
    }
    val image = InputImage.fromMediaImage(media, proxy.imageInfo.rotationDegrees)
    scanner.process(image)
        .addOnSuccessListener { codes ->
            codes.firstNotNullOfOrNull { it.rawValue }?.let(onResult)
        }
        .addOnCompleteListener { proxy.close() }
}
