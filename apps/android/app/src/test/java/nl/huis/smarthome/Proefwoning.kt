package nl.huis.smarthome

// De vaste URL houdt de schermafdrukken van dezelfde proefwoning vergelijkbaar.
private val proefwoningAdres = listOf(192, 168, 1, 20).joinToString(".")
internal val proefwoningUrl = "http://$proefwoningAdres:8088"
