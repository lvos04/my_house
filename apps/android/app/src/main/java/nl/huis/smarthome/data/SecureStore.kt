package nl.huis.smarthome.data

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys
import nl.huis.smarthome.core.Credentials

/** What the app remembers after pairing. */
data class Pairing(
    val token: String,
    val lanUrl: String?,
    val remoteUrl: String?,
    val homeId: String?,
    val homeName: String?,
    val username: String?,
    val role: String?,
)

/**
 * Token and Cloudflare Access secret are stored with EncryptedSharedPreferences
 * (AES-256, key in the Android Keystore). Excluded from backups via data_extraction_rules.
 */
class SecureStore(context: Context) {
    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        "shc_secure",
        MasterKeys.getOrCreate(MasterKeys.AES256_GCM_SPEC),
        context,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    fun pairing(): Pairing? {
        val token = prefs.getString(K_TOKEN, null) ?: return null
        return Pairing(
            token = token,
            lanUrl = prefs.getString(K_LAN, null),
            remoteUrl = prefs.getString(K_REMOTE, null),
            homeId = prefs.getString(K_HOME_ID, null),
            homeName = prefs.getString(K_HOME_NAME, null),
            username = prefs.getString(K_USER, null),
            role = prefs.getString(K_ROLE, null),
        )
    }

    fun savePairing(p: Pairing) = prefs.edit {
        putString(K_TOKEN, p.token)
        putString(K_LAN, p.lanUrl)
        putString(K_REMOTE, p.remoteUrl)
        putString(K_HOME_ID, p.homeId)
        putString(K_HOME_NAME, p.homeName)
        putString(K_USER, p.username)
        putString(K_ROLE, p.role)
    }

    fun updateHome(homeId: String, homeName: String) = prefs.edit {
        putString(K_HOME_ID, homeId)
        putString(K_HOME_NAME, homeName)
    }

    fun cfAccess(): Pair<String?, String?> = prefs.getString(K_CF_ID, null) to prefs.getString(K_CF_SECRET, null)

    fun saveCfAccess(id: String?, secret: String?) = prefs.edit {
        if (id.isNullOrBlank()) remove(K_CF_ID) else putString(K_CF_ID, id.trim())
        if (secret.isNullOrBlank()) remove(K_CF_SECRET) else putString(K_CF_SECRET, secret.trim())
    }

    fun credentials(): Credentials {
        val (id, secret) = cfAccess()
        return Credentials(prefs.getString(K_TOKEN, null), id, secret)
    }

    fun clear() = prefs.edit { clear() }

    private companion object {
        const val K_TOKEN = "token"
        const val K_LAN = "lan_url"
        const val K_REMOTE = "remote_url"
        const val K_HOME_ID = "home_id"
        const val K_HOME_NAME = "home_name"
        const val K_USER = "username"
        const val K_ROLE = "role"
        const val K_CF_ID = "cf_access_client_id"
        const val K_CF_SECRET = "cf_access_client_secret"
    }
}
