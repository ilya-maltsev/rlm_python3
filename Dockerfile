FROM freeradius/freeradius-server:3.2.3-alpine
LABEL maintainer="privacyIDEA FreeRADIUS Python plugin"

COPY raddb/ /etc/raddb/
COPY entrypoint.sh /

# Install the Python plugin
COPY privacyidea_radius.py /usr/share/privacyidea/freeradius/
COPY dictionary.netknights /etc/raddb/dictionary

# Install Python dependencies and FreeRADIUS python module
RUN apk update && apk add --no-cache \
        python3 \
        py3-pip \
        py3-requests \
        py3-chardet \
        freeradius-python3 \
        freeradius-utils

RUN rm -f /etc/raddb/sites-enabled/inner-tunnel \
          /etc/raddb/sites-enabled/default \
          /etc/raddb/mods-enabled/eap
RUN echo 'DEFAULT Auth-Type := python-privacyidea' >> /etc/raddb/users

EXPOSE 1812/udp
EXPOSE 1813/udp
EXPOSE 1812/tcp

ENTRYPOINT ["/entrypoint.sh"]
CMD ["radiusd"]
