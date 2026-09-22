SUMMARY = "Imagen del sistema de control de acceso con video"
LICENSE = "MIT"

inherit core-image

# SSH para entrar sin monitor. debug-tweaks (root sin contraseña) viene del local.conf
# por defecto en Scarthgap: SOLO para desarrollo, quitarlo para la entrega.
IMAGE_FEATURES += "ssh-server-dropbear"

# El bloque ${@ ... } es Python evaluado por BitBake: agrega el grupo CSI solo si CAMARA = "csi"
IMAGE_INSTALL = " \
    packagegroup-core-boot \
    ${CORE_IMAGE_EXTRA_INSTALL} \
    packagegroup-control-acceso-base \
    packagegroup-control-acceso-video \
    ${@'packagegroup-control-acceso-csi' if d.getVar('CAMARA') == 'csi' else ''} \
    packagegroup-control-acceso-gpio \
    packagegroup-control-acceso-debug \
    control-acceso \
"

# 512 MB libres extra en el rootfs (en KB) para grabaciones de prueba
IMAGE_ROOTFS_EXTRA_SPACE = "524288"
