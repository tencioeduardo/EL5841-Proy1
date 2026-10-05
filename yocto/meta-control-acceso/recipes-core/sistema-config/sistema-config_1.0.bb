SUMMARY = "Configuración del sistema: sincronización horaria (NTP)"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://timesyncd-control-acceso.conf"
S = "${WORKDIR}"

# Drop-in de systemd-timesyncd: no reemplaza la configuración de systemd, la complementa.
do_install() {
    install -d ${D}${sysconfdir}/systemd/timesyncd.conf.d
    install -m 0644 ${WORKDIR}/timesyncd-control-acceso.conf \
        ${D}${sysconfdir}/systemd/timesyncd.conf.d/control-acceso.conf
}

FILES:${PN} = "${sysconfdir}/systemd/timesyncd.conf.d"
