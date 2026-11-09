package nl.huis.smarthome.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.OptIn
import androidx.camera.core.CameraSelector
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
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.graphics.Color
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import nl.huis.smarthome.R
import nl.huis.smarthome.data.UnpairResult
import nl.huis.smarthome.ui.CameraPolicy
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.UiPrefs
import nl.huis.smarthome.ui.appSettingsIntent
import nl.huis.smarthome.ui.findActivity
import nl.huis.smarthome.ui.startSafely
import java.util.concurrent.Executors

// The pairing screen is always dark, as in the design.
private val PairBg = Color(0xFF1C1B19)
private val PairPanel = Color(0xFF2A2926)
private val PairText = Color(0xFFF5F4F0)
private val PairMuted = Color(0xFFD6D3CB)
private val PairSubtle = Color(0xFFB8B4AA)

/** Borders of text fields and buttons: 4.7:1 on the background (was 1.9:1). */
private val PairOutline = Color(0xFF8C887F)
private val PairAccent = Color(0xFFE8A27A)
private val PairWarnBg = Color(0xFF3A2E16)
private val PairWarnText = Color(0xFFF2D9A6)
private val PairOkBg = Color(0xFF1F3327)
private val PairOkText = Color(0xFFBFE6CC)

@Composable
fun PairScreen(vm: MainViewModel, storeWasReset: Boolean = false) {
    val pair by vm.pair.collectAsStateWithLifecycle()
    val unpairResult by vm.unpairResult.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val prefs = remember { UiPrefs(context) }
    var manual by rememberSaveable { mutableStateOf(false) }
    var cameraFailed by rememberSaveable { mutableStateOf(false) }
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

    Column(
        Modifier
            .fillMaxSize()
            .background(PairBg)
            // Includes the keyboard: the manual fields stay visible.
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 24.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(stringResource(R.string.pair_step), color = PairSubtle, style = MaterialTheme.typography.bodyMedium)
            Text(
                stringResource(R.string.pair_title),
                color = PairText,
                style = MaterialTheme.typography.headlineMedium,
                modifier = Modifier.semantics { heading() },
            )
            Text(stringResource(R.string.pair_body), color = PairMuted, style = MaterialTheme.typography.bodyLarge)
        }

        when (unpairResult) {
            UnpairResult.REVOKED_ON_SERVER -> PairNote(stringResource(R.string.unpair_revoked), PairOkBg, PairOkText)
            UnpairResult.LOCAL_ONLY -> PairNote(stringResource(R.string.unpair_local_only), PairWarnBg, PairWarnText)
            null -> if (storeWasReset) PairNote(stringResource(R.string.store_reset), PairWarnBg, PairWarnText)
        }

        if (manual) {
            ManualEntry(busy = pair.busy, onSubmit = vm::pairManually)
        } else {
            ScannerPanel(
                hasCamera = hasCamera,
                cameraBlocked = cameraBlocked,
                cameraUnavailable = cameraFailed || !hasCameraHardware,
                busy = pair.busy,
                onRequestCamera = requestCamera,
                onCameraError = { cameraFailed = true },
                onManual = switchMode,
                onScanned = vm::onQrScanned,
            )
        }

        pair.error?.let { error ->
            Column(
                Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(14.dp))
                    .background(PairWarnBg)
                    .semantics { liveRegion = LiveRegionMode.Assertive }
                    .padding(start = 14.dp, end = 6.dp, top = 12.dp, bottom = 4.dp),
            ) {
                Text(error, color = PairWarnText, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(end = 8.dp))
                if (!manual) {
                    // The same rejected QR code is not sent again until the user asks for it.
                    TextButton(
                        onClick = vm::clearPairError,
                        modifier = Modifier.align(Alignment.End).heightIn(min = 48.dp),
                    ) { Text(stringResource(R.string.pair_scan_again), color = PairWarnText) }
                }
            }
        }

        Column(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(16.dp))
                .background(PairPanel)
                .padding(horizontal = 16.dp, vertical = 14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            RouteLine(stringResource(R.string.route_lan), Color(0xFF7FC79B), stringResource(R.string.pair_route_lan))
            RouteLine(stringResource(R.string.route_remote), Color(0xFF9DBAE8), stringResource(R.string.pair_route_remote))
            Text(stringResource(R.string.pair_route_info), color = PairMuted, style = MaterialTheme.typography.bodySmall)
        }

        OutlinedButton(
            onClick = switchMode,
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
            shape = RoundedCornerShape(16.dp),
            border = BorderStroke(1.dp, PairOutline),
        ) {
            Text(
                stringResource(if (manual) R.string.pair_scan_instead else R.string.pair_manual),
                color = PairText,
                style = MaterialTheme.typography.labelLarge,
            )
        }
    }
}

@Composable
private fun PairNote(text: String, bg: Color, fg: Color) {
    Text(
        text,
        color = fg,
        style = MaterialTheme.typography.bodyMedium,
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(bg)
            .padding(14.dp),
    )
}

@Composable
private fun RouteLine(label: String, color: Color, text: String) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(label, color = color, style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.SemiBold))
        Text(text, color = PairMuted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun ScannerPanel(
    hasCamera: Boolean,
    cameraBlocked: Boolean,
    cameraUnavailable: Boolean,
    busy: Boolean,
    onRequestCamera: () -> Unit,
    onCameraError: () -> Unit,
    onManual: () -> Unit,
    onScanned: (String) -> Unit,
) {
    val previewLabel = stringResource(R.string.camera_preview)
    Box(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 320.dp)
            .clip(RoundedCornerShape(28.dp))
            .background(PairPanel),
        contentAlignment = Alignment.Center,
    ) {
        when {
            cameraUnavailable -> CameraMessage(
                text = stringResource(R.string.pair_camera_error),
                button = stringResource(R.string.pair_manual_short),
                onClick = onManual,
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
                Box(
                    Modifier
                        .size(220.dp)
                        .border(4.dp, PairText.copy(alpha = 0.9f), RoundedCornerShape(18.dp)),
                )
                Box(
                    Modifier
                        .size(width = 188.dp, height = 2.dp)
                        .background(PairAccent),
                )
                if (busy) {
                    Column(
                        Modifier
                            .clip(RoundedCornerShape(16.dp))
                            .background(PairBg.copy(alpha = 0.85f))
                            .padding(16.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        CircularProgressIndicator(color = PairText)
                        Text(stringResource(R.string.pair_busy), color = PairText, style = MaterialTheme.typography.bodyMedium)
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
) {
    Column(
        Modifier.padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(text, color = PairMuted, style = MaterialTheme.typography.bodyMedium)
        Button(
            onClick = onClick,
            modifier = Modifier.heightIn(min = 48.dp),
            colors = ButtonDefaults.buttonColors(containerColor = PairText, contentColor = PairBg),
        ) { Text(button) }
        if (secondary != null && onSecondary != null) {
            TextButton(onClick = onSecondary, modifier = Modifier.heightIn(min = 48.dp)) { Text(secondary, color = PairText) }
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
    val colors = OutlinedTextFieldDefaults.colors(
        focusedTextColor = PairText,
        unfocusedTextColor = PairText,
        focusedBorderColor = PairText,
        unfocusedBorderColor = PairOutline,
        focusedLabelColor = PairText,
        unfocusedLabelColor = PairSubtle,
        focusedPlaceholderColor = PairSubtle,
        unfocusedPlaceholderColor = PairSubtle,
        cursorColor = PairText,
    )
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
        Button(
            onClick = submit,
            enabled = canSubmit,
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(containerColor = PairText, contentColor = PairBg),
        ) {
            Text(stringResource(if (busy) R.string.pair_busy else R.string.pair_submit))
        }
    }
}

/**
 * CameraX preview + ML Kit QR analysis bound to the current lifecycle. Any failure to start the
 * camera (no camera, in use, driver error) calls [onError] instead of leaving a black box.
 */
@Composable
private fun QrCamera(modifier: Modifier, onResult: (String) -> Unit, onError: () -> Unit) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val currentOnResult by rememberUpdatedState(onResult)
    val currentOnError by rememberUpdatedState(onError)
    val executor = remember { Executors.newSingleThreadExecutor() }
    val scanner = remember {
        BarcodeScanning.getClient(BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build())
    }
    val providerFuture = remember { runCatching { ProcessCameraProvider.getInstance(context) }.getOrNull() }

    if (providerFuture == null) {
        DisposableEffect(Unit) {
            currentOnError()
            onDispose { }
        }
        return
    }

    AndroidView(
        modifier = modifier,
        factory = { ctx ->
            val view = PreviewView(ctx).apply { scaleType = PreviewView.ScaleType.FILL_CENTER }
            providerFuture.addListener({
                try {
                    val provider = providerFuture.get()
                    val preview = Preview.Builder().build().also { it.setSurfaceProvider(view.surfaceProvider) }
                    val analysis = ImageAnalysis.Builder()
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build()
                        .also { it.setAnalyzer(executor) { proxy -> analyze(proxy, scanner) { value -> currentOnResult(value) } } }
                    provider.unbindAll()
                    provider.bindToLifecycle(lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
                } catch (e: Exception) {
                    currentOnError()
                }
            }, ContextCompat.getMainExecutor(ctx))
            view
        },
    )

    DisposableEffect(Unit) {
        onDispose {
            runCatching { providerFuture.get().unbindAll() }
            scanner.close()
            executor.shutdown()
        }
    }
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
