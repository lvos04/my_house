package nl.huis.smarthome.core

internal fun proefIpv4(eerste: Int, tweede: Int, derde: Int, host: Int): String =
    listOf(eerste, tweede, derde, host).joinToString(".")

internal fun lanProefadres(host: Int): String = proefIpv4(192, 168, 74, host)
