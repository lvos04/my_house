package nl.huis.smarthome.data

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys
import nl.huis.smarthome.core.Credentials
import nl.huis.smarthome.core.UnpairReason
import java.security.KeyStore

/** What the app remembers after pairing. */
data class Pairing(
    val token: String,
    val lanUrl: String?,
    val remoteUrl: String?,
    val homeId: String?,
    val homeName: String?,
    val username: String?,
    /** Role of the user who made the QR code: "admin" | "viewer". */
    val role: String?,
    /** Identity of the paired server; the token only goes to an address that answers with it. */
    val serverId: String? = null,
    /** Role of this phone: "admin" | "control" | "viewer"; null for pairings from before phone roles. */
    val clientRole: String? = null,
    /** ISO time the token expires (refreshed automatically within 30 days of it). */
    val tokenExpiresAt: String? = null,
    /** Last known rights from /auth/me or the WebSocket hello; null = not asked yet. */
    val canControl: Boolean? = null,
    val isAdmin: Boolean? = null,
)

/**
 * Token, addresses and the Cloudflare Access secret, stored with EncryptedSharedPreferences
 * (AES-256, key in the Android Keystore). Excluded from backups via data_extraction_rules.
 *
 * The Keystore can lose or corrupt its key (rare: OS update, restored device). Opening or reading
 * the store then throws; instead of crashing app, widget and worker on every start, the store is
 * wiped and recreated ([wasReset]) and the phone must be paired again.
 */
class SecureStore private constructor(
    private val context: Context,
    @Volatile private var kv: Kv,
    initiallyReset: Boolean,
) {
    @Volatile var wasReset: Boolean = initiallyReset
        private set

    /** Survives app restarts until the next successful pairing. */
    private val flags: SharedPreferences = context.getSharedPreferences(FLAGS, Context.MODE_PRIVATE)

    init {
        if (initiallyReset) flags.edit().putBoolean(K_RESET, true).apply()
        if (flags.getBoolean(K_RESET, false)) wasReset = true
    }

    fun pairing(): Pairing? = guarded<Pairing?>(null) {
        val token = kv.get(K_TOKEN) ?: return@guarded null
        Pairing(
            token = token,
            lanUrl = kv.get(K_LAN),
            remoteUrl = kv.get(K_REMOTE),
            homeId = kv.get(K_HOME_ID),
            homeName = kv.get(K_HOME_NAME),
            username = kv.get(K_USER),
            role = kv.get(K_ROLE),
            serverId = kv.get(K_SERVER_ID),
            clientRole = kv.get(K_CLIENT_ROLE),
            tokenExpiresAt = kv.get(K_EXPIRES),
            canControl = kv.get(K_CAN_CONTROL)?.toBooleanStrictOrNull(),
            isAdmin = kv.get(K_IS_ADMIN)?.toBooleanStrictOrNull(),
        )
    }

    /**
     * Stores a new pairing (and forgets an earlier "niet meer gekoppeld" state). The Cloudflare
     * Access values are kept: re-pairing usually goes through the same tunnel.
     */
    fun savePairing(p: Pairing): Boolean {
        val ok = guarded(false) {
            kv.put(
                mapOf(
                    K_TOKEN to p.token,
                    K_LAN to p.lanUrl,
                    K_REMOTE to p.remoteUrl,
                    K_HOME_ID to p.homeId,
                    K_HOME_NAME to p.homeName,
                    K_USER to p.username,
                    K_ROLE to p.role,
                    K_SERVER_ID to p.serverId,
                    K_CLIENT_ROLE to p.clientRole,
                    K_EXPIRES to p.tokenExpiresAt,
                    K_CAN_CONTROL to p.canControl?.toString(),
                    K_IS_ADMIN to p.isAdmin?.toString(),
                    K_UNPAIRED to null,
                ),
                sync = true,
            )
        }
        if (ok) {
            wasReset = false
            flags.edit().remove(K_RESET).apply()
        }
        return ok
    }

    fun updateHome(homeId: String, homeName: String) = write(mapOf(K_HOME_ID to homeId, K_HOME_NAME to homeName))

    fun updateAddresses(lanUrl: String?, remoteUrl: String?) = write(mapOf(K_LAN to lanUrl, K_REMOTE to remoteUrl))

    fun updateServerId(serverId: String) = write(mapOf(K_SERVER_ID to serverId))

    fun updateAccess(canControl: Boolean?, isAdmin: Boolean?, clientRole: String?, userRole: String?) = write(
        buildMap {
            canControl?.let { put(K_CAN_CONTROL, it.toString()) }
            isAdmin?.let { put(K_IS_ADMIN, it.toString()) }
            clientRole?.let { put(K_CLIENT_ROLE, it) }
            userRole?.let { put(K_ROLE, it) }
        },
    )

    fun updateExpiry(expiresAt: String?) = write(mapOf(K_EXPIRES to expiresAt))

    /**
     * Stores a refreshed token synchronously: the old one is already dead on the server.
     * Returns false when it could not be written (the in-memory value is used for this session).
     */
    fun saveToken(token: String, expiresAt: String?): Boolean = write(mapOf(K_TOKEN to token, K_EXPIRES to expiresAt), sync = true)

    /** Server said the token is revoked/expired/unknown: remember it so the dead token is not sent again. */
    fun markUnpaired(reason: UnpairReason) = write(mapOf(K_UNPAIRED to reason.name))

    fun unpairedReason(): UnpairReason? = guarded<UnpairReason?>(null) {
        kv.get(K_UNPAIRED)?.let { name -> UnpairReason.entries.firstOrNull { it.name == name } }
    }

    fun cfAccess(): Pair<String?, String?> = guarded<Pair<String?, String?>>(null to null) { kv.get(K_CF_ID) to kv.get(K_CF_SECRET) }

    fun saveCfAccess(id: String?, secret: String?) = write(
        mapOf(K_CF_ID to id?.trim()?.takeIf { it.isNotEmpty() }, K_CF_SECRET to secret?.trim()?.takeIf { it.isNotEmpty() }),
    )

    fun credentials(): Credentials = guarded(Credentials()) {
        Credentials(kv.get(K_TOKEN), kv.get(K_CF_ID), kv.get(K_CF_SECRET))
    }

    fun clear() {
        guarded(Unit) { kv.clear() }
    }

    // ---- plumbing ----

    private fun write(values: Map<String, String?>, sync: Boolean = false): Boolean = guarded(false) {
        kv.put(values, sync)
    }

    /** Runs [block]; a crypto/keystore failure resets the store instead of crashing. */
    private inline fun <T> guarded(fallback: T, block: () -> T): T = try {
        block()
    } catch (e: Exception) {
        Log.w(TAG, "Beveiligde opslag onleesbaar; opnieuw aangemaakt", e)
        resetCorrupted()
        fallback
    }

    @Synchronized
    private fun resetCorrupted() {
        kv = recreate(context)
        wasReset = true
        flags.edit().putBoolean(K_RESET, true).apply()
    }

    /** Minimal key-value view so a memory-only fallback is possible. */
    private interface Kv {
        fun get(key: String): String?
        fun put(values: Map<String, String?>, sync: Boolean): Boolean
        fun clear()
    }

    private class PrefsKv(private val prefs: SharedPreferences) : Kv {
        override fun get(key: String): String? = prefs.getString(key, null)

        override fun put(values: Map<String, String?>, sync: Boolean): Boolean {
            val e = prefs.edit()
            values.forEach { (k, v) -> if (v == null) e.remove(k) else e.putString(k, v) }
            return if (sync) e.commit() else {
                e.apply()
                true
            }
        }

        override fun clear() {
            prefs.edit().clear().commit()
        }
    }

    /** Used only when the Keystore does not work at all: pairing then lasts until the app stops. */
    private class MemoryKv : Kv {
        private val map = java.util.concurrent.ConcurrentHashMap<String, String>()
        override fun get(key: String): String? = map[key]
        override fun put(values: Map<String, String?>, sync: Boolean): Boolean {
            values.forEach { (k, v) -> if (v == null) map.remove(k) else map[k] = v }
            return true
        }
        override fun clear() = map.clear()
    }

    companion object {
        private const val TAG = "SecureStore"
        private const val FILE = "shc_secure"
        private const val FLAGS = "shc_store_state"
        private const val MASTER_KEY_ALIAS = "_androidx_security_master_key_"

        private const val K_TOKEN = "token"
        private const val K_LAN = "lan_url"
        private const val K_REMOTE = "remote_url"
        private const val K_HOME_ID = "home_id"
        private const val K_HOME_NAME = "home_name"
        private const val K_USER = "username"
        private const val K_ROLE = "role"
        private const val K_SERVER_ID = "server_id"
        private const val K_CLIENT_ROLE = "client_role"
        private const val K_EXPIRES = "token_expires_at"
        private const val K_CAN_CONTROL = "can_control"
        private const val K_IS_ADMIN = "is_admin"
        private const val K_UNPAIRED = "unpaired_reason"
        private const val K_CF_ID = "cf_access_client_id"
        private const val K_CF_SECRET = "cf_access_client_secret"
        private const val K_RESET = "store_reset"

        /** Never throws: a broken store is wiped and recreated, as a last resort kept in memory. */
        fun open(context: Context): SecureStore {
            val app = context.applicationContext
            return try {
                val prefs = create(app)
                prefs.getString(K_TOKEN, null) // decrypting once surfaces a broken key now, not later
                SecureStore(app, PrefsKv(prefs), initiallyReset = false)
            } catch (e: Exception) {
                Log.w(TAG, "Beveiligde opslag kon niet worden geopend; opnieuw aanmaken", e)
                SecureStore(app, recreate(app), initiallyReset = true)
            }
        }

        private fun create(context: Context): SharedPreferences = EncryptedSharedPreferences.create(
            FILE,
            MasterKeys.getOrCreate(MasterKeys.AES256_GCM_SPEC),
            context,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )

        private fun recreate(context: Context): Kv {
            runCatching { context.deleteSharedPreferences(FILE) }
            runCatching {
                val ks = KeyStore.getInstance("AndroidKeyStore")
                ks.load(null)
                ks.deleteEntry(MASTER_KEY_ALIAS)
            }
            return try {
                PrefsKv(create(context))
            } catch (e: Exception) {
                Log.e(TAG, "Android Keystore werkt niet; koppeling alleen in het geheugen", e)
                MemoryKv()
            }
        }
    }
}
