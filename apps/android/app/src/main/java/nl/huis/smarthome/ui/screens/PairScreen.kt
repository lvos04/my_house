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
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import nl.huis.smarthome.R
import nl.huis.smarthome.ui.MainViewModel
import java.util.concurrent.Executors

// The pairing screen is always dark, as in the design.
private val PairBg = Color(0xFF1C1B19)
private val PairPanel = Color(0xFF2A2926)
private val PairText = Color(0xFFF5F4F0)
private val PairMuted = Color(0xFFD6D3CB)
private val PairSubtle = Color(0xFFB8B4AA)
private val PairOutline = Color(0xFF4A4740)
private val PairAccent = Color(0xFFE8A27A)

@Composable
fun PairScreen(vm: MainViewModel) {
    val pair by vm.pair.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var manual by rememberSaveable { mutableStateOf(false) }
    var hasCamera by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    val cameraLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { hasCamera = it }

    Column(
        Modifier
            .fillMaxSize()
            .background(PairBg)
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 24.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(stringResource(R.string.pair_step), color = PairSubtle, style = MaterialTheme.typography.bodyMedium)
            Text(stringResource(R.string.pair_title), color = PairText, style = MaterialTheme.typography.headlineMedium)
            Text(stringResource(R.string.pair_body), color = PairMuted, style = MaterialTheme.typography.bodyLarge)
        }

        if (manual) {
            ManualEntry(busy = pair.busy, onSubmit = vm::pairManually)
        } else {
            ScannerPanel(
                hasCamera = hasCamera,
                busy = pair.busy,
                onRequestCamera = { cameraLauncher.launch(Manifest.permission.CAMERA) },
                onScanned = vm::onQrScanned,
            )
        }

        pair.error?.let {
            Text(
                it,
                color = Color(0xFFF2D9A6),
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(14.dp))
                    .background(Color(0xFF3A2E16))
                    .padding(14.dp),
            )
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
            onClick = { manual = !manual; vm.clearPairError() },
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
private fun RouteLine(label: String, color: Color, text: String) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(label, color = color, style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.SemiBold))
        Text(text, color = PairMuted, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun ScannerPanel(hasCamera: Boolean, busy: Boolean, onRequestCamera: () -> Unit, onScanned: (String) -> Unit) {
    val previewLabel = stringResource(R.string.camera_preview)
    Box(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 320.dp)
            .clip(RoundedCornerShape(28.dp))
            .background(PairPanel)
            .semantics { contentDescription = previewLabel },
        contentAlignment = Alignment.Center,
    ) {
        when {
            !hasCamera -> Column(
                Modifier.padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text(stringResource(R.string.pair_camera_needed), color = PairMuted, style = MaterialTheme.typography.bodyMedium)
                Button(
                    onClick = onRequestCamera,
                    colors = ButtonDefaults.buttonColors(containerColor = PairText, contentColor = PairBg),
                ) { Text(stringResource(R.string.pair_camera_allow)) }
            }
            else -> {
                // Same QR is delivered many times per second; forward each value at most every 3 s.
                var last by remember { mutableStateOf<String?>(null) }
                var lastAt by remember { mutableLongStateOf(0L) }
                QrCamera(Modifier.matchParentSize()) { value ->
                    val now = System.currentTimeMillis()
                    if (value != last || now - lastAt > 3_000) {
                        last = value
                        lastAt = now
                        onScanned(value)
                    }
                }
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
                if (busy) CircularProgressIndicator(color = PairText)
            }
        }
    }
}

@Composable
private fun ManualEntry(busy: Boolean, onSubmit: (String, String) -> Unit) {
    var address by rememberSaveable { mutableStateOf("") }
    var code by rememberSaveable { mutableStateOf("") }
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
            colors = colors,
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = { onSubmit(address, code) },
            enabled = !busy && address.isNotBlank() && code.isNotBlank(),
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(containerColor = PairText, contentColor = PairBg),
        ) {
            Text(stringResource(if (busy) R.string.pair_busy else R.string.pair_submit))
        }
    }
}

/** CameraX preview + ML Kit QR analysis bound to the current lifecycle. */
@Composable
private fun QrCamera(modifier: Modifier, onResult: (String) -> Unit) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val executor = remember { Executors.newSingleThreadExecutor() }
    val scanner = remember {
        BarcodeScanning.getClient(BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build())
    }
    val providerFuture = remember { ProcessCameraProvider.getInstance(context) }

    AndroidView(
        modifier = modifier,
        factory = { ctx ->
            val view = PreviewView(ctx).apply { scaleType = PreviewView.ScaleType.FILL_CENTER }
            providerFuture.addListener({
                val provider = providerFuture.get()
                val preview = Preview.Builder().build().also { it.setSurfaceProvider(view.surfaceProvider) }
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                    .also { it.setAnalyzer(executor) { proxy -> analyze(proxy, scanner, onResult) } }
                runCatching {
                    provider.unbindAll()
                    provider.bindToLifecycle(lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
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
