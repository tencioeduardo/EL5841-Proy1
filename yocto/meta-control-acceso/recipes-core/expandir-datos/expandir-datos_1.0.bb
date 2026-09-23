SUMMARY = "Expande la partición de datos hasta el final de la microSD en el primer arranque"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://expandir-datos.sh file://expandir-datos.service"
S = "${WORKDIR}"

inherit systemd
SYSTEMD_SERVICE:${PN} = "expandir-datos.service"
SYSTEMD_AUTO_ENABLE = "enable"

RDEPENDS:${PN} = "parted e2fsprogs-e2fsck e2fsprogs-resize2fs"

do_install() {
    install -d ${D}${sbindir}
    install -m 0755 ${WORKDIR}/expandir-datos.sh ${D}${sbindir}/expandir-datos
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/expandir-datos.service ${D}${systemd_system_unitdir}/
}
