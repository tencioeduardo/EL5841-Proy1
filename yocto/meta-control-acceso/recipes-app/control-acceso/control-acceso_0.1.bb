SUMMARY = "Aplicación de control de acceso (placeholder hasta integrar el pipeline real)"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

# Archivos locales en files/. Cuando el código viva en un repo git se cambia a:
# SRC_URI = "git://github.com/USUARIO/REPO.git;protocol=https;branch=main"
SRC_URI = "file://control_acceso.py \
           file://control-acceso.service"
S = "${WORKDIR}"

inherit systemd
SYSTEMD_SERVICE:${PN} = "control-acceso.service"
SYSTEMD_AUTO_ENABLE = "enable"

RDEPENDS:${PN} = "python3-core"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${WORKDIR}/control_acceso.py ${D}${bindir}/control-acceso
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/control-acceso.service ${D}${systemd_system_unitdir}/
}
