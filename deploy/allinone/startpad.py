"""Toon het vaste formulierargument voor precies de ingebouwde workersoftware."""

from shc_api.connector_transition import LOCAL_PROPOSAL

if __name__ == "__main__":
    print("shc_allinone.start_" + LOCAL_PROPOSAL.rsplit(":", 1)[1])
