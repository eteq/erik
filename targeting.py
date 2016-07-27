from __future__ import division, print_function

"""
These functions are for the "Distant local groups" project target selection.
"""
#important note: SDSS 'type' field: 3=galaxy, 6=star

import numpy as np
from matplotlib import pyplot as plt

try:
    import six
except ImportErrir:
    from astropy.extern import six
urlopen = six.moves.urllib.request.urlopen

from astropy import units as u


SDSS_IMAGE_LIST_URL = 'http://skyserver.sdss.org/dr12/en/tools/chart/list.aspx'

# the color cuts specified in the BOSSANOVA proposal
bossanova_color_cuts = {'g-r': (None, 1.3), 'r-i': (None, 0.7)}
# more stringent color cuts useful for prioritizing targeting
tighter_color_cuts = {'g-r': (None, 1.0), 'r-i': (None, 0.5)}


def select_targets(host, band='r', faintlimit=21, brightlimit=15,
    galvsallcutoff=20, inclspecqsos=False, removespecstars=True,
    removegalsathighz=True, removegama='now', photflags=True,
    outercutrad=250, innercutrad=20, colorcuts={},
    randomize=True, removeallsdss=False, fibermagcut=('r', 23),
    verbose=False, catalog=None):
    """
    Selects targets from the SDSS catalog.

    Parameters
    ----------
    host : NSAHost
        The host object to select targets for
    band : str
        The name of the photometric band to use for magnitude cuts
    faintlimit : number
        The faint cutoff in `band` magnitudes
    brightlimit : number
        The bright cutoff in `band` magnitudes
    galvsallcutoff : number
        The cutoff below which both stars and galaxies are used
    inclspecqsos : bool
        Whether or not to include SDSS spectroscopic targets classified as QSOs
    removespecstars : bool
        Whether or not to ignore SDSS spectroscopic targets classified as stars
    removegalsathighz : bool or velocity Quantity
        Whether or not to ignore SDSS spectroscopic targets classified as
        galaxies but with redshfits far from host.   Far means > 3*zerr +z_host
        if this is a bool (and True), otherwise, the Quantity specifies the
        velocity offest.
    removegama : str
        If not empty string/False, don't select targets that are already in
        GAMA.  Can be 'all' or 'now' to remove targets that will *eventually* be
        in GAMA, or just those currently observed. Note that the tolerance is
        currently 1 arcsec, which seems fine based on a check of one field.
    photflags : bool
        Apply some photometry flags (see the code for exactly which, or
        http://www.sdss3.org/dr9/tutorials/flags.php for description).
        Depending on the situation, these might already have been done in the
        original SDSS query.
    outercutrad : Quantity or None
        A separation angle or distance beyond which to not select targets
        or None to not do this cut
    innercutrad : number or None
        A separation angle in *kpc* inside which to not select targets
        (or negative for arcmin), or None to not cut
    colorcuts: dict of 2-tuples
        See `colorcut_mask` inputs for what this should be
    randomize : bool
        Randomize the order of the catalog and the very end
    removeallsdss : bool
        If True, removes *all* SDSS spectra from the target selection
    fibermagcut : None or 2-tuple
        a 2-tuple ('band', mag) to remove objects with a fibermag fainter than
        `mag`, or None to do nothing about this.
    verbose : bool
        Print extra information
    catalog : `astropy.table.Table` or None
        If None, use the `get_sdss_catalog` from the host.  Otherwise, should
        be something that looks like that.

    Returns
    -------
        cat : astropy.table.Table
            The SDSS catalog with the selection applied
    """
    from astropy.constants import c
    from astropy.table import Column, MaskedColumn
    from math import cos, radians

    if catalog is None:
        cat = host.get_sdss_catalog()
    else:
        cat = catalog

    mag = cat[band]

    #raw magnitude cuts
    magcuts = (brightlimit < mag) & (mag < faintlimit)

    #color cuts if any are present
    colorcuts = colorcuts.copy()
    colorcutmsk = colorcut_mask(cat, colorcuts, verbose)


    #type==3 is an imaging-classified galaxy - but only do it if you're brighter than galvsallcutoff
    nonphotgal = (cat['type'] == 3) | (mag > galvsallcutoff)

    #base selection is based on the above
    msk = magcuts & colorcutmsk & nonphotgal

    cdec = cos(radians(host.dec))


    if 'rhost' not in cat.colnames:
        dra = cat['ra'] - host.ra
        ddec = cat['dec'] - host.dec
        rhost = ((dra * cdec) ** 2 + ddec ** 2) ** 0.5
        colcls = MaskedColumn if hasattr(rhost, 'mask') else Column
        cat.add_column(colcls(name='rhost', data=rhost))
        cat.add_column(colcls(name='rhost_kpc', data=np.radians(rhost)*host.distmpc*1000))

    #negative for arcmin
    if outercutrad is not None:
        if hasattr(outercutrad, 'unit') and outercutrad.unit.is_equivalent(u.Mpc):
            outercutraddeg = np.degrees(outercutrad.to(u.Mpc).value / host.distmpc)
        elif hasattr(outercutrad, 'unit') and outercutrad.unit.is_equivalent(u.degree):
            outercutraddeg = outercutrad.to(u.degree).value
        elif isinstance(outercutrad, float) or isinstance(outercutrad, int):  # pre-Quantity
            if outercutrad < 0:  # arcmin
                outercutraddeg = -outercutrad / 60.
            else:  # kpc
                outercutraddeg = np.degrees(outercutrad / (1000 * host.distmpc))
        else:
            raise ValueError('Invalid outercutrad')
    else:
        outercutraddeg = cat['rhost'].max()

    outercutrad = cat['rhost'] < outercutraddeg

    msk = msk & outercutrad

    if innercutrad is not None:
        if innercutrad < 0:  # arcmin
            innercutraddeg = -innercutrad / 60.
        else:  # kpc
            innercutraddeg = np.degrees(innercutrad / (1000 * host.distmpc))

        innercutrad = cat['rhost'] > innercutraddeg

        msk = msk & innercutrad

    if photflags:
        flags = cat['flags']
        binned1 = (flags & 0x10000000) != 0  # BINNED1 detection
        nsaturated = (0x0000000000040000 & flags) == 0  # not saturated
        nbce = (0x0000010000000000 & flags) == 0  # not BAD_COUNTS_ERROR
        #photqual = (flags & 0x8100000c00a0) == 0  # not NOPROFILE, PEAKCENTER,
            # NOTCHECKED, PSF_FLUX_INTERP, SATURATED, or BAD_COUNTS_ERROR
        #deblendnopeak = ((flags & 0x400000000000) == 0)  # | (psfmagerr_g <= 0.2)  # DEBLEND_NOPEAK
        msk = msk & binned1 & nsaturated & nbce

    #below are "overrides" rather than selection categories:

    #include SDSS spectroscopy QSOs
    if inclspecqsos:
        specqsos = cat['spec_class'] == 'QSO'
        msk[specqsos] = inclspecqsos
        print('Found', sum(specqsos), 'QSO candidates')

    if removespecstars:
        specstars = cat['spec_class'] == 'STAR'
        msk[specstars] = False

    if removegalsathighz:
        gals = cat['spec_class'] == 'GALAXY'
        if (u.km/u.s).is_equivalent(removegalsathighz):
            # take this to just mean it has to be within the given cutoff of the host
            zthresh = (host.zspec + removegalsathighz/c).decompose().value
        else:
            #here,  "high" z means more than 3sigma above the host's redshift in quadrature with the galaxy's spec error
            zthresh = (host.zspec + 3 * np.hypot(host.zdisterr, cat['spec_z_err']))
        highzgals = gals & ((cat['spec_z']) > zthresh)
        lowzgals = gals & ((cat['spec_z']) <= zthresh)
        validspec = cat['spec_z_warn'] == 0
        print('Removing {0} objects at high z w/ good spectra, keeping {1} (total of {2} objects)'.format(highzgals.sum(), lowzgals.sum(), len(lowzgals)))
        msk[highzgals&validspec] = False

    if removeallsdss:
        sdssspecs = ~cat['spec_class'].mask
        print('Removing ALL objects with SDSS spec: {0} of {1} objects'.format(sdssspecs.sum(), len(sdssspecs)))
        msk[sdssspecs] = False

    if removegama:
        g = get_gama()
        if (host.dec + outercutraddeg > g.decmax or
            host.dec - outercutraddeg < g.decmin or
            host.ra + outercutraddeg > g.ramax or
            host.ra - outercutraddeg < g.ramin):
            pass#print('Host not in GAMA area - not looking at GAMA')
        else:
            print('Found host', host.name, 'in GAMA!')
            if removegama == 'all':
                future = True
            elif removegama == 'now':
                future = False
            else:
                raise ValueError('invalid removegama')

            gamamatchmsk = find_gama(cat, host, outercutraddeg, tol=1 / 3600.,
                matchfuture=future)[0]

            msk = msk & ~gamamatchmsk
            print('Removing', np.sum(gamamatchmsk),'GAMA objects')

    if fibermagcut:
        fmagname = 'fibermag_' + fibermagcut[0]
        msk = msk & (cat[fmagname] < fibermagcut[1])

    res = cat[msk]
    if randomize:
        res = res[np.random.permutation(len(res))]

    finalmsk = np.ones(len(res), dtype=bool)
    return res[finalmsk]


def colorcut_mask(cat, colorcuts, deredden=True, verbose=False):
    """
    Apply color cuts to a photometry catalog

    Parameters
    ----------
    cat : astropy.table.Table
        The catalog to actually select from
    colorcuts: dict of 2-tuples
        A dictionary mapping SDSS colors to the range of colors to accept as
        ``(bluest, reddest)``. E.g., {'g-r': (-1, 2)}.  Default is to do no
        color cuts. dict vals can also be (lower, upper, uncfactor) to allow
        for an `uncfactor` of the uncertainty padding around the limits.  Can
        also have an entry 'funcs' which is a list of functions accepting the
        catalog as the sole argument and outputing masks to add more complex
        color cuts
    deredden : bool
        Use dereddened/extinction-corrected colors instead of raw colors
    verbose : bool
        If True, prints out what each cut does

    Returns
    -------
    colorcutmsk : bool array
        True for catalog entries that pass the color cuts, False for those that
        do not.
    """
    colorcutmsk = np.ones(len(cat), dtype=bool)  # start by accepting everything
    if colorcuts:
        for k, v in colorcuts.items():
            if k == 'funcs':
                for i, f in enumerate(v):
                    fmsk = f(cat)
                    if verbose:
                        print('Function', i, 'removed', np.sum(~fmsk), 'objects')
                    colorcutmsk = colorcutmsk & fmsk
            else:
                c1, c2 = k.split('-')
                if deredden:
                    color = (cat[c1] - cat['A'+c1]) - (cat[c2] - cat['A'+c2])
                else:
                    color = cat[c1] - cat[c2]

                if len(v) == 3:
                    bluec, redc, uncfactor = v

                    c1e = c1+'_err'
                    if c1e not in cat.colnames:
                        c1e = c1+'err'
                    c2e = c2+'_err'
                    if c2e not in cat.colnames:
                        c2e = c2+'err'

                    uncfactor = uncfactor * (cat[c1e]**2 + cat[c2e]**2)**0.5

                else:
                    uncfactor = 0
                    bluec, redc = v
                if bluec is None:
                    bluec = -float('inf')
                if redc is None:
                    redc = float('inf')

                assert bluec < redc, 'blue cut larger than red cut!: ' + str(v)

                #this now adds the cuts to the mask for this color
                cmsk = ((bluec-uncfactor) < color) & (color < (redc+uncfactor))
                if verbose:
                    print('Colorcut for', k, 'removed', np.sum(~cmsk), 'objects')
                colorcutmsk = colorcutmsk & cmsk
    return colorcutmsk


def find_gama(cat, host, raddeg, tol, matchfuture=True, whichgama='DR1'):
    """
    Find GAMA objects that match the given ra/decs within a tolerance

    Parameters
    ----------
    catra : record array
        The catalog of objects to match
    host : NSAHost
        The host
    raddeg : array
        The radius of the field in degrees
    tol : float
        The distance (in degrees) of a "close enough" match.
    matchfuture : bool
        If True, include things that are planned for future GAMA releases.
        Otherwise, only accepts things that are currently in GAMA
    whichgama : str or Table
        Either 'DR1' or 'DR2' or a table as from 'get_gama'

    Returns
    -------
    msk : bool array
        An array that's true if the catalog entry has a matching GAMA object
    gamacat : Table
        The corresponding GAMA entries
    ds : float array
        On sky-distances (w/o cos(dec)) between GAMA and SDSS
    """
    from scipy import spatial

    catra = cat['ra']
    catdec = cat['dec']
    ra0 = host.ra
    dec0 = host.dec

    if isinstance(whichgama, six.string_types):
        g = get_gama(url=whichgama)
    else:
        g = whichgama

    #first try to figure out the relevant GAMA column names
    gamacols = dict(granm=('RA_J2000', 'RA'),
                    gdecnm=('DEC_J2000', 'DEC'),
                    gznm=('Z_HELIO', 'Z'),
                    gzqnm=('Z_QUALITY', 'NQ'))
    for varnm, colnms in gamacols.items():
        for colnm in colnms:
            if colnm in g.dtype.names:
                gamacols[varnm] = colnm
                break
        else:
            msg = 'Could not find any of {0} while looking for {1}'
            raise ValueError(msg.format(colnms, varnm))
    granm = gamacols['granm']
    gdecnm = gamacols['gdecnm']
    gznm = gamacols['gznm']
    gzqnm = gamacols['gzqnm']

    gamacoverage = ((g[granm] < (ra0 + raddeg)) &
                    (g[granm] > (ra0 - raddeg)) &
                    (g[gdecnm] < (dec0 + raddeg)) &
                    (g[gdecnm] > (dec0 - raddeg)))

    if matchfuture:
        gamaspec = g[gzqnm] > 2
    else:
        gamaspec = (g[gznm] > -2) & (g[gzqnm] > 2)

    gm = g[gamacoverage & gamaspec]
    kdt = spatial.cKDTree(np.array([gm[granm], gm[gdecnm]]).T)

    ds, idx = kdt.query(np.array([catra, catdec]).T)

    msk = ds < tol
    return msk, gm[idx[msk]], ds[msk]


def usno_vs_sdss_offset(sdsscat, usnocat, plots=False, raiseerror=0.5):
    """
    Determines the offset between a USNO-B1 catalog and a sdss catalog covering
    the same fields by matching nearby stars and computing the median.

    Parameters
    ----------
    sdsscat : astropy.table.Table
        Table from the SDSS catalog - must have 'ra' and 'dec'
    usnocat : astropy.table.Table
        Table from the SDSS catalog - must have 'RA' and 'DEC'
    plots : bool
        True to show diagnostic plots
    raiseerror : float or None
        The distance to raise an error if the resulting offset is more than the
        given number of arcmin.

    Returns
    -------
    dra : array
    ddec : array

    Raises
    ------
    ValueError
        If the separation is larger that `raiseerror`
    """
    from scipy.spatial import cKDTree
    from math import cos

    sra = sdsscat['ra']
    sdec = sdsscat['dec']
    ura = usnocat['RA']
    udec = usnocat['DEC']

    kdt = cKDTree(np.array([sra, sdec]).T)

    d, si = kdt.query(np.array([ura, udec]).T)

    dra = ura - sra[si]
    ddec = udec - sdec[si]

    newura = ura - np.median(dra)
    newudec = udec - np.median(ddec)

    d2, si2 = kdt.query(np.array([newura, newudec]).T)

    dra2 = ura - sra[si2]
    ddec2 = udec - sdec[si2]
    d2off = np.hypot(dra2, ddec2)

    cdec = cos(np.radians(np.median(udec)))

    if plots:
        plt.figure()
        bins = np.linspace(0, 3, 200)
        plt.hist(d * 3600, bins=bins, histtype='step')
        plt.hist(d2off * 3600, bins=bins, histtype='step')
        plt.xlabel('d [arcmin]')
        plt.figure()
        plt.plot(dra * 3600, ddec * 3600, '.b', ms=1, alpha=.5)
        plt.plot(dra2 * 3600, ddec2 * 3600, '.r', ms=1, alpha=.5)
        plt.scatter([0], [0], color='k', zorder=2)
        plt.scatter([np.median(dra) * 3600], [np.median(ddec) * 3600], color='r', alpha=.95, zorder=2)
        plt.scatter([np.median(dra2) * 3600], [np.median(ddec2) * 3600], color='g', alpha=.95, zorder=2)
        plt.xlim(-1 / cdec, 1 / cdec)
        plt.ylim(-1, 1)

    dres = np.hypot(np.median(dra2) / cdec, np.median(ddec2))
    if raiseerror is not None and ((dres * 3600) > raiseerror):
        raise ValueError('median separation from USNO to SDSS is {0} arcsec'.format(dres * 3600))

    return np.median(dra2), np.median(ddec2)


def sampled_imagelist(ras, decs, n=25, names=None, url=SDSS_IMAGE_LIST_URL,
    copytoclipboard=False, posttoimglist=3.):
    """
    Returns the text to be pasted into the sdss image list page.  Also opens
    the page (if `url` is not None) and copies the text to the clipboard if on
    a mac or linux.

    Parameters
    ----------
    ras : array-like or astropy.table.Table or astropy coordinate
        RA of objects to be marked in degrees or a table with 'ra' and 'dec'
        columnds if ``decs`` is None, or an astropy coordinate object  with
        ra and dec if `decs` is None.
    decs : array-like or None
        Dec of objects to be marked in degrees, or None if ``ras`` is to be
        treated as a table
    n : int
        Maximum number of objects (randomly sampled if this is greater than
        `ras` or `decs` length)
    url : str or None
        The URL to the SDSS image list page or None to not open in a web
        browser.
    copytoclipboard : bool
        If True, copies the list of images to the clipboard for use on the SDSS
        web site
    posttoimglist : bool or float
        If True, makes a form to post to the URL site. If a float, gives the
        number of seconds to wait until deleting the temporary file (to gives
        the browser time to load).

    Returns
    -------
    text : str
        The table to be pasted into the image list text box

    """
    import webbrowser
    import platform
    import tempfile
    import time
    import os

    if decs is None:
        if hasattr(ras, 'ra') and hasattr(ras, 'dec'):
            decs = ras.dec
            ras = ras.ra
        else:
            # assume it's a Table
            if 'DEC' in ras.colnames and 'RA' in ras.colnames:
                decs = ras['DEC']
                ras = ras['RA']
            else:
                decs = ras['dec']
                ras = ras['ra']

    if len(ras) != len(decs):
        raise ValueError('ras and decs not the same size!')

    ras = np.array(ras, copy=False)
    decs = np.array(decs, copy=False)

    idx = None
    if len(ras) > n:
        idx = np.random.permutation(len(ras))[:n]
        ras = ras[idx]
        decs = decs[idx]

    if names is None:
        names = [str(i) for i in range(len(ras))]
    elif idx is not None:
        names = np.array(names, copy=False)[idx]

    text = ['name ra dec']
    for nmi, rai, deci in zip(names, ras, decs):
        text.append('{0} {1} {2}'.format(nmi, rai, deci))
    text = '\n'.join(text)

    if copytoclipboard:
        if platform.system() == 'Darwin':
            clipproc = os.popen('pbcopy', 'w')
            clipproc.write(text)
            clipproc.close()
        elif platform.system() == 'Linux':
            clipproc = os.popen('xsel -i', 'w')
            clipproc.write(text)
            clipproc.close()
        else:
            print("Not on a mac or linux, so can't use clipboard. ")

    if url:
        if posttoimglist:
            page = _imglist_post_templ.format(url=url,text=text)
            tf = tempfile.NamedTemporaryFile(delete=False)
            tf.write(page)
            tf.flush()
            fiurl = 'file://' + os.path.abspath(tf.name)
            webbrowser.open(fiurl)
            if isinstance(posttoimglist, float):
                time.sleep(posttoimglist)
        else:
            webbrowser.open(url)

    return text
_imglist_post_templ = """
<html>
<head>
<title>SDSS sampled_imagelist form</title>
</head>
<body>
<h1> SDSS sampled_imagelist form </h1>
<p>Using URL {url}</p>

<form action="{url}"
method="post">
<TEXTAREA name="paste">
{text}
</TEXTAREA>
<br>
Scale: <input class="in" type="text" value="0.4" name="scale">
<br>
Opt: <input class="in" type="text" value="" name="opt">
<br>
<input type="submit">
</form>
</body>
</html>
"""


def sdss_IAU_id_to_ra_dec(sdssids, matchtocatalog=None):
    """
    Converts IAU SDSS identifiers (e.g.,"J151503.37+421253.9") to their RA/Decs

    Returns an ICRS object if `matchtocatalog` is None, otherwise
    `catalogidx, skysep`

    """
    import re
    from astropy.coordinates import ICRS, Latitude, Longitude

    rex = re.compile(r'J(\d{2})(\d{2})(\d{2}(?:\.\d{1,2})?)'
                     r'([+-])(\d{2})(\d{2})(\d{2}(?:\.\d)?)')

    if isinstance(sdssids, six.string_types):
        sdssids = [sdssids]

    ras = []
    decs = []
    for idi in sdssids:
        res = rex.match(idi)
        if res is None:
            raise ValueError('Could not match ' + idi)
        res = res.groups()
        rah, ram, rasc = res[:3]
        desgn, ded, dem, des = res[-4:]

        ras.append(':'.join((rah, ram, rasc)))
        decs.append(desgn + ':'.join((ded, dem, des)))

    coords = ICRS(ra=Longitude(ras, u.hourangle), dec=Latitude(decs, u.degree))
    if matchtocatalog:
        if not hasattr(matchtocatalog, 'match_to_catalog_sky'):
            matchtocatalog = ICRS(ra=np.array(matchtocatalog['ra'])*u.deg,
                                  dec=np.array(matchtocatalog['dec'])*u.deg)
        idx, sep, sep3d = coords.match_to_catalog_sky(matchtocatalog)
        return idx, sep.to(u.arcsec)
    else:
        return coords


_DEFAULT_TREM_URL = 'http://docs.google.com/spreadsheets/d/1Y3nO7VyU4jDiBPawCs8wJQt2s_PIAKRj-HSrmcWeQZo/export?format=csv&gid=1379081675'
def remove_targets_with_remlist(cat, hostorhostname,
                                listfnorurl=_DEFAULT_TREM_URL,
                                matchtol=0.1*u.arcsec, maskonly=False, verbose=True):
    """
    Use either a local csv copy, or a URL to the google spreadsheet of the
    target remove list to remove manually/by-eye filtered targets.

    Parameters
    ----------
    cat : astropy.table.Table
        The target catalog to remove from.  Typically the output of
        `select_targets`, but can be anything with 'ra' and 'dec' fields.
    hostorhostname : str or NSAHost
        The name of the host to use, or the host object (in which case its)
        name will come from the object.
    listfnorurl : str
        A local path to a csv file or a URL to the google spreadsheet that has
        the target remove list.
    matchtol : astropy Quantity
        How close the match has to be if the objid search fails
    maskonly : bool
        If True, return the mask into the catalog (mask value=True to *not*
        remove). False returns a sub-selected catalog
    verbose : bool or 'warning'
        Print informational messages.  If 'warning', only prints if no host
        matched.
    """
    from astropy.coordinates import SkyCoord


    hostname = getattr(hostorhostname, 'name', hostorhostname)
    if hasattr(hostorhostname, 'nsaid'):
        nsanum = hostorhostname.nsaid
    elif hostname.startswith('NSA'):
        nsanum = int(hostname[3:])
    else:
        nsanum = None


    objids = cat['objID']
    catcoords = SkyCoord(cat['ra']*u.deg, cat['dec']*u.deg)

    objidstoremove = []

    if listfnorurl.startswith('http://'):
        uo = urlopen(listfnorurl)
        try:
            remlist_content = uo.read()
        finally:
            uo.close()
    else:
        with open(listfnorurl) as f:
            remlist_content = f.read()

    nmatched = 0
    for i, l in enumerate(remlist_content.split('\n')[2:]):  # [2:] is to skip header lines
        if l.replace(',', '').strip() == '':
            continue  # empty line

        ls = l.split(',')
        if ls[0] == hostname or (nsanum is not None and ls[1]!='' and int(ls[1]) == nsanum):
            #try to find it in the catalog, first by objid, and if not, by ra/dec
            objid = six.integer_types[-1](ls[2]) #integer_types[-1] is long on py2
            if objid in objids:
                objidstoremove.append(objid)
                nmatched += 1
            else:
                objcoord = SkyCoord(float(ls[3])*u.deg, float(ls[4])*u.deg)
                idx, sep, sep3d = objcoord.match_to_catalog_sky(catcoords)
                if sep < matchtol:
                    objidstoremove.append(objid)
                    nmatched += 1
                else:
                    msg = 'Could not find a match for objid {0} of {1}, closest is {2}'
                    print(msg.format(objid, hostname, sep.to(u.arcsec)))

    if verbose and nmatched == 0:
        print('No matches found for host "{0}" in remove list. Maybe you mis-typed something?'.format(hostname))
    elif verbose and verbose != 'warning':
        print('Removed', nmatched, 'objects for', hostname)

    msk = ~np.in1d(objids, objidstoremove)
    if maskonly:
        return msk
    else:
        return cat[msk]


_cachedgama = {}
def get_gama(fn=None, url='DR1'):
    """
    Download or load the GAMA survey data

    Parameters
    ----------
    fn : str or None
        A file to load from or None to use the default.
    url : str
        The URL to get the GAMA catalog from.  Or can be 'DR1' or 'DR2' to get
        the spectroscopic data from the standard locations.

    Returns
    -------
    gamadata
        The data as an astropy `Table`.  Table will also have `decmax`,
        `decmin`, `ramax`, and `ramin`.
    """
    import os

    from astropy.io import ascii, fits
    from hosts import download_with_progress_updates

    URLMAP = {
    'DR1': 'http://www.gama-survey.org/dr1/data/GamaCoreDR1_v1.csv.gz',
    'DR2': 'http://www.gama-survey.org/dr2/data/cat/SpecCat/v08/SpecObj.fits'}

    url = URLMAP.get(url, url)

    if fn is None:
        fn = os.path.split(url)[-1]
        if not fn.lower().startswith('gama'):
            fn = 'GAMA' + fn
        fn = os.path.join('catalogs', fn)


    if fn in _cachedgama:
        return _cachedgama[fn]

    if not os.path.exists(fn):
        with open(fn, 'w') as fw:
            msg = 'Downloading GAMA from ' + url + ' to ' + fn
            u = urlopen(url)
            try:
                download_with_progress_updates(u, fw, msg=msg)
            finally:
                u.close()

    if '.fits' in fn:
        tab = _cachedgama[fn] = fits.getdata(fn, 1)
    elif '.csv' in fn:
        tab = _cachedgama[fn] = ascii.read(fn, delimiter=',', guess=False)
    else:
        raise ValueError('Unrecognized file type for GAMA file:' + str(fn))

    if 'RA_J2000' in tab.dtype.names:
        rastr = 'RA_J2000'
        decstr = 'DEC_J2000'
    else:
        rastr = 'RA'
        decstr = 'DEC'

    tab.ramax = np.max(tab[rastr])
    tab.ramin = np.min(tab[rastr])
    tab.decmax = np.max(tab[decstr])
    tab.decmin = np.min(tab[decstr])

    return tab

def add_forced_targets(rawcat, targcat, pris, toforce, pritoforceto, matchtol=1*u.arcsec):
    """
    This takes an input catalog
    """
    from astropy.coordinates import SkyCoord
    from astropy.table import vstack

    if isinstance(toforce, SkyCoord):
        #match based on the closest w/i matchtol
        catscs = SkyCoord(rawcat['ra']*u.deg, rawcat['dec']*u.deg)

        idx, d2d, d3d = toforce.match_to_catalog_sky(catscs)
        matches = d2d < matchtol

        if np.any(matches):
            print('Found', np.sum(matches), 'coordinate matches in list of things to force')
            toadd = rawcat[idx[matches]]
        else:
            print('Found NO coordinate matches in list of things to force!')
            toadd = None
    else:
        #it's the objID
        matches = np.in1d(rawcat['objID'], toforce)
        if np.sum(matches)>0:
            print('Found', np.sum(matches), 'objID matches in list of things to force')
            toadd = rawcat[matches]
        else:
            print('Found NO objID matches in list of things to force!')
            toadd = None

    if toadd is not None:
        #remove any duplicates
        dups = np.in1d(targcat['objID'], toadd['objID'])
        if np.sum(dups) > 0:
            print('Removed', np.sum(dups), 'Forced doubles (things that were already possible targets *and* forced)')
            targcat = targcat[~dups]
            pris = pris[~dups]

        # now add them back in with the right pri
        targcat = vstack([targcat, toadd])
        pris = np.concatenate([pris, [pritoforceto]*len(toadd)])

    return targcat, pris


def find_duplicate_objids(catalog, idxtokeep=0):
    """
    Finds duplicate objIDs in the `catalog` and return a boolean array that is
    True if it's a duplicate.  `idxtokeep` tells which of the duplicates to keep
    (or None to keep none of them)
    """
    #now look for duplicates and always take the first - usually means multiple WISE or 2MASS matches
    uids, ucnts = np.unique(catalog['objID'], return_counts=True)
    dupstorem = np.zeros(len(catalog), dtype=bool)
    for i in uids[ucnts>1]:
        dupstorem[np.where(catalog['objID'] == i)[0]] = True
        if idxtokeep is not None:
            dupstorem[np.where(catalog['objID'] == i)[0][idxtokeep]] = False
    return dupstorem
