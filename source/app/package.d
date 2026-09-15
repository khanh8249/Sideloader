module app;

import file = std.file;
import std.path;
import std.process : environment;

import slf4d;

import provision;

struct ProvisioningData {
    Device device;
    ADI adi;  // luôn null — không dùng nữa
}

// === Đọc URL anisette từ env, fallback VPS ===
string anisetteServerUrl() {
    auto url = environment.get("ANISETTE_URL");
    if (url is null || url.length == 0) {
        url = "https://anisette-v3-server-sg29.onrender.com/";
    }
    if (url[$ - 1] != '/') url ~= '/';
    return url;
}

// === Chỉ tạo Device, KHÔNG load native, KHÔNG provision local ===
ProvisioningData initializeADI(string configurationPath) {
    auto log = getLogger();
    auto device = new Device(configurationPath.buildPath("device.json"));

    if (!device.initialized) {
        log.info("Creating device...");

        import std.digest;
        import std.random;
        import std.range;
        import std.uni;
        import std.uuid;
        // Bỏ com.apple.dt.Xcode — Apple block Client-Info này
        device.serverFriendlyDescription = "<MacBookPro18,3> <Mac OS X;26.5.2> <com.apple.AuthKit/1 (com.apple.akd/1)>";
        device.uniqueDeviceIdentifier = randomUUID().toString().toUpper();
        device.adiIdentifier = (cast(ubyte[]) rndGen.take(2).array()).toHexString().toLower();
        device.localUserUUID = (cast(ubyte[]) rndGen.take(8).array()).toHexString().toUpper();
        log.info("Device created successfully.");
    }
    log.debug_("Device OK.");

    log.infoF!"Using remote anisette server: %s"(anisetteServerUrl());

    return ProvisioningData(device, null);
}
