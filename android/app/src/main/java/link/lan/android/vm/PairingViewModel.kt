package link.lan.android.vm

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import link.lan.android.data.SecureStore
import link.lan.app.InviteCheck
import link.lan.app.KnownDevice
import link.lan.app.PairingFlow
import link.lan.app.PairingStep
import link.lan.app.Presented
import link.lan.app.examineInvite

/**
 * Pairing, on a worker thread, driven entirely by [PairingFlow].
 *
 * The order is not re-implemented here and must not be: probe the certificate,
 * confirm it, then send the code. [PairingFlow] refuses a code that arrives out
 * of turn whatever this class does, and every button below is only allowed to
 * ask it for the next step.
 *
 * The one thing this adds is which kind of confirmation applies. A QR invite
 * carrying the full fingerprint is checked by [PairingFlow.confirmAgainst] —
 * the phone compares, not the person. An address typed by hand has nothing to
 * compare against, so the fingerprint goes on screen and a human says yes.
 */
data class PairingUiState(
    val step: PairingStep = PairingStep.IDLE,
    val target: String = "",
    val presented: Presented? = null,
    val expectedFingerprint: String = "",
    val codeFromInvite: String = "",
    val problem: String? = null,
    val paired: KnownDevice? = null,
    val working: Boolean = false,
) {
    val fingerprint: String get() = presented?.short.orEmpty()
    val needsHumanConfirmation: Boolean
        get() = step == PairingStep.CONFIRMING && expectedFingerprint.isEmpty()
    val awaitingCode: Boolean get() = step == PairingStep.AWAITING_CODE
}

class PairingViewModel(application: Application) : AndroidViewModel(application) {

    private val store = SecureStore.open(application)
    private var flow: PairingFlow? = null

    private val _state = MutableStateFlow(PairingUiState())
    val state: StateFlow<PairingUiState> = _state.asStateFlow()

    /**
     * Step 0: read what was scanned, pasted or typed.
     *
     * Returns the refusal to show, or null when pairing has started. Nothing
     * has touched the network at this point — the invite is judged first.
     */
    fun begin(text: String): String? {
        val checked = examineInvite(text, store.load(), allowInsecure = store.allowInsecureInvites)
        return when (checked) {
            is InviteCheck.Rejected -> checked.reason
            is InviteCheck.AlreadyPaired -> checked.message
            is InviteCheck.Ready -> {
                val invite = checked.invite
                flow = PairingFlow(
                    host = invite.host,
                    port = invite.port,
                    clientId = store.clientId(),
                    clientName = store.deviceName,
                )
                _state.value = PairingUiState(
                    step = PairingStep.PROBING,
                    target = checked.label,
                    expectedFingerprint = checked.pinnedFingerprint,
                    codeFromInvite = checked.codeFromInvite,
                    working = true,
                )
                probe()
                null
            }
        }
    }

    /** Step 1: fetch the certificate. Blocking, so never on the main thread. */
    private fun probe() {
        val current = flow ?: return
        viewModelScope.launch {
            val shown = withContext(Dispatchers.IO) { current.probeCertificate() }
            val expected = _state.value.expectedFingerprint

            // A pinned invite is checked here rather than by the person. The
            // comparison itself lives in PairingFlow so it cannot be skipped.
            if (shown != null && expected.isNotEmpty()) {
                val agreed = withContext(Dispatchers.IO) { current.confirmAgainst(expected) }
                publish(working = false)
                if (agreed && _state.value.codeFromInvite.isNotEmpty()) {
                    submit(_state.value.codeFromInvite)
                }
                return@launch
            }
            publish(working = false)
        }
    }

    /** Step 2, by eye: the person compared the fingerprint with the PC's. */
    fun confirmByEye(matches: Boolean) {
        val current = flow ?: return
        current.confirmFingerprint(matches)
        publish()
        if (matches && _state.value.codeFromInvite.isNotEmpty()) {
            submit(_state.value.codeFromInvite)
        }
    }

    /** Step 3: send the code. [PairingFlow] refuses it unless step 2 happened. */
    fun submit(code: String) {
        val current = flow ?: return
        _state.value = _state.value.copy(working = true, problem = null)
        viewModelScope.launch {
            val device = withContext(Dispatchers.IO) { current.submitCode(code) }
            publish(working = false)
            if (device != null) {
                _state.value = _state.value.copy(paired = device)
            }
        }
    }

    fun cancel() {
        flow?.cancel()
        flow = null
        _state.value = PairingUiState()
    }

    private fun publish(working: Boolean = _state.value.working) {
        val current = flow ?: return
        _state.value = _state.value.copy(
            step = current.step,
            presented = current.presented,
            problem = current.problem,
            working = working,
        )
    }
}
