import gtk, gobject, cairo
from StringIO import StringIO
import dbus.mainloop.glib

from .__init__ import MailIconBase, AccountIconDataBase
import logging

import threading

gtk.threads_init()

def make_pixbuf(base, color, label):
    # Convert the base image to a Cairo surface
    size = base.get_width()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    gdkctx = gtk.gdk.CairoContext(cr)
    gdkctx.set_source_pixbuf(base, 0, 0)
    gdkctx.paint()

    # Measure text
    my_str = unicode(label)
    cr.move_to(size, size)
    cr.select_font_face('sans', cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(10.0*size/22)
    extents = cr.text_extents(my_str) # x_bearing,y_bearing, width,height, ...
    #print label, extents
    text_width = extents[2] #+extents[0]
    text_height = extents[3] #-extents[1]
    text_margin = 2

    # Draw a color-coded 3D box around the text
    cr.set_line_width(1.0*size/22)
    rect_width = text_width+text_margin*2-1
    rect_height = text_height+text_margin*2
    # Bottom and left
    cr.move_to(size-.5, size-.5-rect_height)
    cr.rel_line_to(0, rect_height)
    cr.rel_line_to(-rect_width, 0)
    cr.set_source_rgb(0, 0, 0) #r*0.5, r*0.5, r*0.5)
    cr.stroke()
    # Top and right
    cr.move_to(size-0.5, size-0.5-rect_height)
    cr.rel_line_to(-rect_width, 0)
    cr.rel_line_to(0, rect_height)
    cr.set_source_rgb(1, 1, 1) #min(1,r*1.5), min(1,r*1.5), min(1, r*1.5))
    cr.stroke()
    # Fill
    cr.set_source_rgb(color[0], color[1], color[2])
    cr.rectangle(size-0.5, size-0.5, -rect_width, -rect_height)
    cr.fill()
    # Alternative: get colors from the GTK style

    # Determine whether black or white text will contrast better.
    # Via http://24ways.org/2010/calculating-color-contrast/
    yiq = color[0]*0.299 + color[1]*0.587 + color[2]*0.114
    if yiq >= .5:
        cr.set_source_rgb(0, 0, 0)
    else:
        cr.set_source_rgb(1, 1, 1)

    # Draw the text
    cr.move_to(size-text_width-text_margin-1, size-text_margin)
    #cr.move_to((size-text_width-1)/2.0, (size+text_height)/2.0)
    cr.show_text(my_str)

    # Render back to a GdkPixbuf via a PNG (!)
    png = StringIO()
    surface.write_to_png(png)
    loader = gtk.gdk.PixbufLoader()
    loader.write(png.getvalue())
    # Is this sufficient to ensure the image is completely loaded?
    loader.close()
    return loader.get_pixbuf()

def update_timer(timer, interval, callback):
    if timer is not None:
        gobject.source_remove(timer)
    return gobject.timeout_add(interval, callback) \
        if interval is not None else None

MAIN_THREAD = threading.current_thread()

class AccountIconDataGtk(AccountIconDataBase):
    def __init__(self, account, config):
        super(AccountIconDataGtk, self).__init__(account, config)
        # Parse color
        color = gtk.gdk.color_parse(config['color'])
        self.color = (color.red_float, color.green_float, color.blue_float)
        self.pixbuf = None

class MailIconGtk(MailIconBase):
    def __init__(self, app):
        self.timer_checkobs = None
        self.timer_cycle = None
        super(MailIconGtk, self).__init__(app)
        self.cycler = None
        self.base = None
        self._account_data_class = AccountIconDataGtk

        self.icon = gtk.StatusIcon()
        try:
            self.icon.set_title("MultiOwl")
        except AttributeError:
            pass
        #self._resize_icon(self.icon, 1)
        self.icon.connect('size-changed', self._resize_icon)

    #     gobject.timeout_add(2000, self._debug)
    # def _debug(self):
    #     self.log.debug("Embedded=%s, Visible=%s" % (self.icon.is_embedded(),
    #                                                 self.icon.get_visible()))
    #     return True

    def _update(self, *args, **kwargs):
        self.update(*args, **kwargs)
        return False            # Do not call again

    def _resize_icon(self, icon, size):
        assert threading.current_thread() == MAIN_THREAD
        self.log.debug("Now %d pixels" % size)
        self.base = gtk.gdk.pixbuf_new_from_file_at_size('mail.svg',
                                                         size, size)
        if not self.base:
            self.log.error("Can't load icon")
        #self.base = gtk.gdk.pixbuf_new_from_icon_name('mail-unread')
        icon.set_from_pixbuf(self.base)
        # Wait for icon to realize, else bad things happen (the Cairo text
        # API will have trouble with text)
        gobject.idle_add(self._update)
        return True

    def refresh_tooltip(self, heading, accounts):
        if heading:
            tooltip = '<b>%s</b>' % (heading,)
            if accounts:
                tooltip += '\n  '.join([''] + accounts)
        else:
            tooltip = ''
        self.icon.set_tooltip_markup(tooltip)

    def refresh_display(self):
        if self.displaying:
            pixbuf = self.app.accounts[self.displaying].icondata.pixbuf
            self.icon.set_from_pixbuf(pixbuf)
            self.icon.set_visible(True)
        else:
            self.icon.set_visible(False)

    def refresh_account_icon(self, account):
        if not self.base:
            return
        account.icondata.pixbuf = make_pixbuf(self.base,
                                              account.icondata.color,
                                              account.count)

    def _check_obsolete(self):
        self.check_obsolete()
        return True

    def check_obsolete_timer(self, interval):
        self.timer_checkobs = update_timer(self.timer_checkobs, interval,
                                           self._check_obsolete)

    def _cycle(self):
        self.cycle()
        return True

    def cycle_timer(self, interval):
        self.timer_cycle = update_timer(self.timer_cycle, interval,
                                        self._cycle)

    def notify(self, account=None):
        gobject.idle_add(self._update, account)

MailIcon = MailIconGtk

class MultiowlConfigurator(gtk.Dialog):
    def __init__(self, app):
        super(MultiowlConfigurator, self).__init__("Multiowl Preferences", \
            buttons=(gtk.STOCK_CLOSE, gtk.RESPONSE_ACCEPT))
        self.connect('response', self.dismiss)

    def show(self):
        self.show_all()
    def dismiss(self, dialog, response):
        self.hide()


def main():
    gtk.main()
