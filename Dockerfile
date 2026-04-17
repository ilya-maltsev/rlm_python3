FROM freeradius/freeradius-server:3.2.3-alpine
LABEL maintainer="privacyIDEA FreeRADIUS Python plugin"

COPY raddb/ /etc/raddb/
COPY entrypoint.sh /

# Install the Python plugin
COPY privacyidea_radius.py /usr/share/privacyidea/freeradius/
COPY dictionary.netknights /etc/raddb/dictionary

# Install Python runtime and dependencies
# rlm_python3.so is already included in the base freeradius image
RUN apk update && apk add --no-cache \
        python3 \
        py3-requests \
        py3-chardet


RUN rm -f /etc/raddb/sites-enabled/inner-tunnel \
          /etc/raddb/sites-enabled/default \
          /etc/raddb/mods-enabled/eap
RUN echo 'DEFAULT Auth-Type := python-privacyidea' >> /etc/raddb/users

EXPOSE 1812/udp
EXPOSE 1813/udp
EXPOSE 1812/tcp

ENTRYPOINT ["/entrypoint.sh"]
CMD ["radiusd"]
