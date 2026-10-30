package com.georgewaller.audiobrain.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.shareIn

/**
 * The phone's default network, watched so screens can ask the server again when it changes. Tailscale connecting
 * makes its VPN the default network, so coming back onto the tailnet counts, as does Wi-Fi returning.
 */
class NetworkWatch(context: Context, api: ApiClient) {
    private val connectivity = context.getSystemService(ConnectivityManager::class.java)

    private val current: Flow<Network?> = callbackFlow {
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) { trySend(network) }
            override fun onLost(network: Network) { trySend(null) }
        }
        trySend(connectivity.activeNetwork)
        connectivity.registerDefaultNetworkCallback(callback)
        awaitClose { connectivity.unregisterNetworkCallback(callback) }
    }

    /** Each time a different network becomes the default (not the one there at start). */
    val changes: Flow<Unit> = current.distinctUntilChanged().drop(1).filterNotNull()
        // connections pooled on the old route can hang until they time out; start afresh on the new one
        .onEach { api.dropConnections() }
        .map { }
        .shareIn(CoroutineScope(SupervisorJob() + Dispatchers.Default), SharingStarted.WhileSubscribed())
}
